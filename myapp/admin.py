from django.contrib import admin
from django.urls import path
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import reverse
from django.core.files.base import ContentFile
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.db.models import Q
from django.forms import ModelForm, ValidationError, CheckboxSelectMultiple
from .models import Workflow, Character, CharacterImage, CharacterCatalogImage, ConnectionConfig, CompanySettings, HeroCarouselImage, CharacterCategory, CharacterSubCategory, ClientProfile, TokenSettings, Coupon
from .services import generate_image_from_character, get_active_comfyui_address, get_comfyui_object_info, analyze_workflow
import json
from asgiref.sync import async_to_sync, sync_to_async
from django.http import JsonResponse

# --- USER CUSTOMIZATION ---

admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=True)

class ClientUser(User):
    class Meta:
        proxy = True
        verbose_name = 'Client'
        verbose_name_plural = 'Clients'

class AdminUser(User):
    class Meta:
        proxy = True
        verbose_name = 'Administrator'
        verbose_name_plural = 'Administrators'

# --- CUSTOM ACTIONS FOR CLIENTS ---
@admin.action(description='Activate selected users')
def activate_users(modeladmin, request, queryset):
    updated = queryset.update(is_active=True)
    modeladmin.message_user(request, f"{updated} users were successfully activated.", level='success')

@admin.action(description='Deactivate selected users')
def deactivate_users(modeladmin, request, queryset):
    updated = queryset.update(is_active=False)
    modeladmin.message_user(request, f"{updated} users were successfully deactivated.", level='success')

# --- INLINE FOR CLIENT PROFILE (READ-ONLY TOKENS) ---
class ClientProfileInline(admin.StackedInline):
    model = ClientProfile
    can_delete = False
    verbose_name_plural = 'Token Usage'
    fk_name = 'user'
    readonly_fields = ('tokens_used', 'last_reset_date', 'tokens_remaining_display')
    fields = ('tokens_used', 'last_reset_date', 'tokens_remaining_display')

    def tokens_remaining_display(self, obj):
        return obj.tokens_remaining
    tokens_remaining_display.short_description = "Tokens Remaining (Based on Global Settings)"

@admin.register(ClientUser)
class ClientUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_active', 'date_joined', 'get_tokens_remaining')
    list_filter = ('is_active', 'date_joined')
    actions = [activate_users, deactivate_users] 
    inlines = [ClientProfileInline] 

    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal Information', {'fields': ('first_name', 'last_name', 'email')}),
        ('Important Dates', {'fields': ('last_login', 'date_joined')}),
        ('Status', {'fields': ('is_active',)}),
    )
    
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=False)

    def get_tokens_remaining(self, obj):
        try:
            return obj.clientprofile.tokens_remaining
        except ClientProfile.DoesNotExist:
            return "N/A"
    get_tokens_remaining.short_description = 'Tokens Remaining'

@admin.register(AdminUser)
class AdminUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'is_staff', 'is_superuser')
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=True)

# --- END USER CUSTOMIZATION ---

# --- GLOBAL TOKEN SETTINGS REGISTRATION ---
@admin.register(TokenSettings)
class TokenSettingsAdmin(admin.ModelAdmin):
    list_display = ('default_token_allowance', 'reset_interval')
    
    def has_add_permission(self, request):
        # Only allow creating if none exists
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

# --- INLINE FOR HERO CAROUSEL IMAGES ---
class HeroCarouselImageInline(admin.TabularInline):
    model = HeroCarouselImage
    extra = 1
    fields = ('image_preview', 'image', 'caption', 'order')
    readonly_fields = ('image_preview',)
    verbose_name = "Hero Carousel Image"
    verbose_name_plural = "Hero Carousel Images"

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="height: 100px; width: auto; border-radius: 5px;" />', obj.image.url)
        return "(No image)"
    image_preview.short_description = "Preview"

@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    inlines = [HeroCarouselImageInline]

    # Organize fields into sections
    fieldsets = (
        ('Company Identity', {
            'fields': ('name', 'logo', 'favicon', 'offer_bar_text', 'description')
        }),
        ('Main Page (Hero)', {
            'fields': ('app_hero_title', 'app_hero_description'),
            'description': 'Upload images for the main carousel in the section below.'
        }),
        ('Contact & Social', {
            'fields': ('phone', 'email', 'facebook', 'discord')
        }),
    )

    def has_add_permission(self, request):
        if self.model.objects.exists(): return False
        return super().has_add_permission(request)
    def has_delete_permission(self, request, obj=None): return False

@admin.register(ConnectionConfig)
class ConnectionConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_url', 'is_active')
    list_editable = ('is_active',)
    list_display_links = ('name',)

@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ('name', 'download_file', 'workflow_actions')
    
    def download_file(self, obj):
        if obj.json_file:
            return format_html('<a href="{}" download>Download JSON</a>', obj.json_file.url)
        return "No file"
    download_file.short_description = 'Download'

    def workflow_actions(self, obj):
        return format_html(
            '<a class="button" href="{}">Configure</a>',
            reverse('admin:workflow_configure', args=[obj.pk])
        )
    workflow_actions.short_description = 'Actions'
    workflow_actions.allow_tags = True

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:workflow_id>/configure/', self.admin_site.admin_view(self.configure_view), name='workflow_configure'),
        ]
        return custom_urls + urls

    def configure_view(self, request, workflow_id):
        workflow = get_object_or_404(Workflow, pk=workflow_id)
        
        try:
            address = async_to_sync(get_active_comfyui_address)()
            comfyui_info = async_to_sync(get_comfyui_object_info)(address)
        except Exception:
            comfyui_info = {"checkpoints": [], "vaes": [], "loras": [], "samplers": [], "schedulers": []}

        try:
            with open(workflow.json_file.path, 'r', encoding='utf-8') as f:
                prompt_workflow = json.load(f)
            workflow_params = analyze_workflow(prompt_workflow)
        except Exception as e:
            self.message_user(request, f"Error loading JSON file: {e}", level='error')
            return redirect('admin:myapp_workflow_changelist')

        saved_config = {}
        if workflow.active_config:
            try:
                saved_config = json.loads(workflow.active_config)
                if 'checkpoint' in saved_config: workflow_params['checkpoint'] = saved_config['checkpoint']
                if 'vae' in saved_config: workflow_params['vae'] = saved_config['vae']
                if 'width' in saved_config: workflow_params['width'] = saved_config['width']
                if 'height' in saved_config: workflow_params['height'] = saved_config['height']
                if 'seed' in saved_config: workflow_params['seed'] = saved_config['seed']
                if 'upscale_by' in saved_config: workflow_params['upscale_by'] = saved_config['upscale_by'] # NEW
                
                if 'lora_names' in saved_config and 'lora_strengths' in saved_config:
                    workflow_params['loras'] = []
                    for name, strength in zip(saved_config['lora_names'], saved_config['lora_strengths']):
                        if name and name != "None":
                            workflow_params['loras'].append({'name': name, 'strength': strength})
            except json.JSONDecodeError: pass

        if request.method == 'POST':
            new_config = {
                'checkpoint': request.POST.get('checkpoint'),
                'vae': request.POST.get('vae'),
                'width': request.POST.get('width'),
                'height': request.POST.get('height'),
                'seed': request.POST.get('seed'),
                'seed_behavior': request.POST.get('seed_behavior', 'random'),
                'upscale_by': request.POST.get('upscale_by'), # NEW
                'lora_names': request.POST.getlist('lora_name'),
                'lora_strengths': request.POST.getlist('lora_strength'),
                'prompt': request.POST.get('prompt'), 
            }
            new_config = {k: v for k, v in new_config.items() if v is not None}
            workflow.active_config = json.dumps(new_config)
            workflow.save()
            self.message_user(request, "Configuration saved successfully.")
            return redirect('admin:myapp_workflow_changelist')

        context = {
            'workflow': workflow,
            'workflow_params': workflow_params,
            'comfyui_info': comfyui_info,
            'saved_config': saved_config, 
            **self.admin_site.each_context(request), 
        }
        return render(request, 'admin/myapp/workflow/configure.html', context)

@admin.register(CharacterImage)
class CharacterImageAdmin(admin.ModelAdmin):
    list_display = ('image_preview', 'character', 'user', 'generation_type_badge', 'download_workflow_link')
    list_filter = ('character', 'user', 'generation_type')
    search_fields = ('description', 'user__username', 'character__name')
    readonly_fields = ('image_preview', 'character', 'user', 'description', 'image', 'generation_type', 'width', 'height', 'download_workflow_link')

    def image_preview(self, obj):
        if obj.image:
            url = reverse('serve_private_media', kwargs={'path': obj.image.name}) if obj.user else obj.image.url
            return format_html('<img src="{}" width="100" height="auto" />', url)
        return "(No image)"
    image_preview.short_description = 'Thumbnail'

    def generation_type_badge(self, obj):
        colors = {
            'Gen_Normal': '#3b82f6',      # Blue
            'Gen_UpScaler': '#10b981',    # Green
            'Gen_FaceDetailer': '#f43f5e' # Red/Pink
        }
        color = colors.get(obj.generation_type, '#6b7280') # Gray default
        label = obj.get_generation_type_display()
        
        return format_html(
            '<span style="background-color: {}; color: white; padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8rem;">{}</span>',
            color,
            label
        )
    generation_type_badge.short_description = 'Type'
    generation_type_badge.admin_order_field = 'generation_type'

    # --- NEW: Workflow download link ---
    def download_workflow_link(self, obj):
        if obj.generation_workflow:
            return format_html('<a href="{}" download>Download JSON</a>', obj.generation_workflow.url)
        return "Not available"
    download_workflow_link.short_description = 'Workflow'

    def has_add_permission(self, request): return False
    def has_delete_permission(self, request, obj=None): return True

class CharacterCatalogImageInline(admin.TabularInline):
    model = CharacterCatalogImage
    extra = 1
    fields = ('image_preview', 'image', 'order')
    readonly_fields = ('image_preview',)
    verbose_name = "Catalog Image"
    verbose_name_plural = "Catalog Images (Upload New)"

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="height: 100px; width: auto; border-radius: 5px;" />', obj.image.url)
        return "(No image)"
    image_preview.short_description = "Preview"

# --- NEW: CATEGORY AND SUBCATEGORY REGISTRATION ---
@admin.register(CharacterCategory)
class CharacterCategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(CharacterSubCategory)
class CharacterSubCategoryAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

# --- CUSTOM ACTIONS FOR CHARACTERS ---
@admin.action(description='Activate selected characters')
def activate_characters(modeladmin, request, queryset):
    updated = queryset.update(is_active=True)
    modeladmin.message_user(request, f"{updated} characters were successfully activated.", level='success')

@admin.action(description='Deactivate selected characters')
def deactivate_characters(modeladmin, request, queryset):
    updated = queryset.update(is_active=False)
    modeladmin.message_user(request, f"{updated} characters were successfully deactivated.", level='success')

@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'subcategory', 'base_workflow', 'is_active', 'character_actions') # ADDED: is_active
    list_filter = ('is_active', 'category', 'subcategory', 'base_workflow') # ADDED: is_active
    actions = [activate_characters, deactivate_characters] # ADDED: actions
    inlines = [CharacterCatalogImageInline]
    # REMOVED: filter_horizontal = ('tags',) (No longer ManyToMany)

    fieldsets = (
        (None, {'fields': ('name', 'description', 'is_active', 'category', 'subcategory', 'base_workflow')}), # ADDED: is_active
        ('Default Prompts (Sandwich)', {
            'fields': ('prompt_prefix', 'prompt_suffix', 'negative_prompt'),
            'description': 'Structure: [Prefix] + (User:1.2) + [Suffix]'
        }),
        ('Advanced Configuration', {'classes': ('collapse',), 'fields': ('character_config',)}),
    )
    readonly_fields = ('character_config',)

    def character_actions(self, obj):
        return format_html(
            '<a class="button" href="{}">Configure</a>',
            reverse('admin:character_configure', args=[obj.pk])
        )
    character_actions.short_description = 'Actions'
    character_actions.allow_tags = True

    def save_model(self, request, obj, form, change):
        if not obj.pk and not obj.character_config and obj.base_workflow.active_config:
            obj.character_config = obj.base_workflow.active_config
        super().save_model(request, obj, form, change)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:character_id>/configure/', self.admin_site.admin_view(self.configure_character_view), name='character_configure'),
            path('<int:character_id>/generate/', self.admin_site.admin_view(self.generate_character_image_view), name='character_generate'),
        ]
        return custom_urls + urls

    def configure_character_view(self, request, character_id):
        character = get_object_or_404(Character, pk=character_id)
        workflow = character.base_workflow
        
        try:
            address = async_to_sync(get_active_comfyui_address)()
            comfyui_info = async_to_sync(get_comfyui_object_info)(address)
        except Exception:
            comfyui_info = {"checkpoints": [], "vaes": [], "loras": [], "samplers": [], "schedulers": []}

        try:
            with open(workflow.json_file.path, 'r', encoding='utf-8') as f:
                prompt_workflow = json.load(f)
            workflow_params = analyze_workflow(prompt_workflow)
        except Exception as e:
            self.message_user(request, f"Error loading base workflow JSON file: {e}", level='error')
            return redirect('admin:myapp_character_changelist')

        saved_config = {}
        if character.character_config:
            try:
                saved_config = json.loads(character.character_config)
                if 'checkpoint' in saved_config: workflow_params['checkpoint'] = saved_config['checkpoint']
                if 'vae' in saved_config: workflow_params['vae'] = saved_config['vae']
                if 'width' in saved_config: workflow_params['width'] = saved_config['width']
                if 'height' in saved_config: workflow_params['height'] = saved_config['height']
                if 'seed' in saved_config: workflow_params['seed'] = saved_config['seed']
                if 'upscale_by' in saved_config: workflow_params['upscale_by'] = saved_config['upscale_by'] # NEW
                
                if 'lora_names' in saved_config and 'lora_strengths' in saved_config:
                    workflow_params['loras'] = []
                    for name, strength in zip(saved_config['lora_names'], saved_config['lora_strengths']):
                        if name and name != "None":
                            workflow_params['loras'].append({'name': name, 'strength': strength})
            except json.JSONDecodeError: pass

        if request.method == 'POST':
            new_config = {
                'checkpoint': request.POST.get('checkpoint'),
                'vae': request.POST.get('vae'),
                # 'width': request.POST.get('width'), # REMOVED
                # 'height': request.POST.get('height'), # REMOVED
                # 'seed': request.POST.get('seed'), # REMOVED
                # 'seed_behavior': request.POST.get('seed_behavior', 'random'), # REMOVED
                # 'upscale_by': request.POST.get('upscale_by'), # REMOVED
                'lora_names': request.POST.getlist('lora_name'),
                'lora_strengths': request.POST.getlist('lora_strength'),
                'prompt': request.POST.get('prompt'), 
            }
            
            # --- NEW: Preserve original values (Read-Only) ---
            # These fields are not read from POST, but kept from previous config or base workflow
            for field in ['width', 'height', 'seed', 'seed_behavior', 'upscale_by']:
                if field in saved_config:
                    new_config[field] = saved_config[field]
                elif workflow_params.get(field):
                    new_config[field] = workflow_params[field]

            new_config = {k: v for k, v in new_config.items() if v is not None}
            character.character_config = json.dumps(new_config)
            character.save()
            self.message_user(request, "Character configuration saved successfully.")
            return redirect('admin:myapp_character_changelist')

        context = {
            'character': character,
            'workflow': workflow,
            'workflow_params': workflow_params,
            'comfyui_info': comfyui_info,
            'saved_config': saved_config,
            'readonly_params': True, # NEW: General indicator to lock fields
            **self.admin_site.each_context(request), 
        }
        return render(request, 'admin/myapp/workflow/configure.html', context)

    def generate_character_image_view(self, request, character_id):
        character = get_object_or_404(Character, pk=character_id)
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if request.method == 'POST':
            prompt = request.POST.get('prompt')
            if not character.character_config:
                msg = "Character has no configuration."
                if is_ajax: return JsonResponse({'status': 'error', 'message': msg})
                self.message_user(request, msg, level='error')
            else:
                try:
                    images_data_list, prompt_id = async_to_sync(generate_image_from_character)(character, prompt)
                    
                    if images_data_list:
                        count = 0
                        for i, (img_bytes, classification) in enumerate(images_data_list):
                            new_image = CharacterImage(
                                character=character, 
                                description=prompt,
                                user=request.user
                            )
                            image_filename = f"generated_{character.name}_{prompt_id}_{classification}_{i}.png"
                            new_image.image.save(image_filename, ContentFile(img_bytes), save=True)
                            count += 1
                        
                        if is_ajax: return JsonResponse({'status': 'success'})
                        self.message_user(request, f"{count} images generated and saved successfully.")
                    else:
                        msg = "Generation did not produce an image."
                        if is_ajax: return JsonResponse({'status': 'error', 'message': msg})
                        self.message_user(request, msg, level='warning')
                except Exception as e:
                    msg = f"Error during generation: {e}"
                    if is_ajax: return JsonResponse({'status': 'error', 'message': msg})
                    self.message_user(request, msg, level='error')
            
            return redirect('admin:myapp_character_change', character_id)

        return redirect('admin:myapp_character_change', character_id)

@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'tokens', 'is_redeemed', 'redeemed_by', 'created_at')
    list_filter = ('is_redeemed', 'created_at')
    search_fields = ('code', 'redeemed_by__username')
    readonly_fields = ('code', 'is_redeemed', 'redeemed_by', 'redeemed_at', 'created_at')

    def has_add_permission(self, request):
        return True

    def save_model(self, request, obj, form, change):
        if not change: # Only on create
            obj.save()
