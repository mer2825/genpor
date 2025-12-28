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
from django.forms import ModelForm, ValidationError
from .models import Workflow, Character, CharacterImage, ConnectionConfig, CompanySettings
from .services import generate_image_from_character, get_active_comfyui_address, get_comfyui_object_info, analyze_workflow
import json
from asgiref.sync import async_to_sync, sync_to_async
from django.http import JsonResponse

# --- PERSONALIZACIÓN DE USUARIOS ---

admin.site.unregister(User)

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=True)

class ClientUser(User):
    class Meta:
        proxy = True
        verbose_name = 'Cliente'
        verbose_name_plural = 'Clientes'

class AdminUser(User):
    class Meta:
        proxy = True
        verbose_name = 'Administrador'
        verbose_name_plural = 'Administradores'

@admin.register(ClientUser)
class ClientUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_active', 'date_joined')
    list_filter = ('is_active', 'date_joined')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Información Personal', {'fields': ('first_name', 'last_name', 'email')}),
        ('Fechas Importantes', {'fields': ('last_login', 'date_joined')}),
        ('Estado', {'fields': ('is_active',)}),
    )
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=False)

@admin.register(AdminUser)
class AdminUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'is_staff', 'is_superuser')
    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=True)

# --- FIN PERSONALIZACIÓN DE USUARIOS ---

# Formulario personalizado para validar el límite de personajes
class CompanySettingsForm(ModelForm):
    class Meta:
        model = CompanySettings
        fields = '__all__'

    def clean_hero_characters(self):
        chars = self.cleaned_data['hero_characters']
        if len(chars) > 6:
            raise ValidationError("No puedes seleccionar más de 6 personajes para el carrusel.")
        return chars

@admin.register(CompanySettings)
class CompanySettingsAdmin(admin.ModelAdmin):
    form = CompanySettingsForm
    list_display = ('name', 'hero_mode', 'email')
    filter_horizontal = ('hero_characters',) # Mejor interfaz para ManyToMany
    
    # Campos de solo lectura (incluida la vista previa)
    readonly_fields = ('hero_carousel_preview',)

    # Organizar campos en secciones
    fieldsets = (
        ('Identidad de la Empresa', {
            'fields': ('name', 'logo', 'description')
        }),
        ('Página Principal (Hero)', {
            'fields': ('app_hero_title', 'app_hero_description', 'hero_mode', 'hero_characters', 'hero_carousel_preview'),
            'description': 'Configura el texto y los personajes del carrusel principal. Usa "Guardar y continuar" para actualizar la vista previa.'
        }),
        ('Contacto y Redes', {
            'fields': ('phone', 'email', 'facebook', 'discord')
        }),
    )

    def has_add_permission(self, request):
        if self.model.objects.exists(): return False
        return super().has_add_permission(request)
    def has_delete_permission(self, request, obj=None): return False

    # --- FUNCIÓN DE VISTA PREVIA ---
    def hero_carousel_preview(self, obj):
        if not obj or not obj.pk:
            return "Guarda la configuración primero para ver la previsualización."
        
        # Obtener personajes seleccionados
        chars = obj.hero_characters.all()
        if not chars:
            return "No hay personajes seleccionados."

        html = '<div style="display: flex; gap: 15px; flex-wrap: wrap; margin-top: 10px;">'
        
        for char in chars:
            # Buscar la primera imagen pública (misma lógica que en la vista)
            img = char.images.filter(Q(user__isnull=True) | Q(user__is_staff=True)).first()
            
            if img:
                html += f'''
                    <div style="text-align: center; background: #1e293b; padding: 10px; border-radius: 8px; border: 1px solid #334155;">
                        <img src="{img.image.url}" style="height: 120px; width: 120px; object-fit: cover; border-radius: 5px; margin-bottom: 5px;">
                        <div style="font-weight: bold; color: #fff; font-size: 12px;">{char.name}</div>
                    </div>
                '''
            else:
                html += f'''
                    <div style="text-align: center; background: #1e293b; padding: 10px; border-radius: 8px; border: 1px solid #334155; width: 120px; display: flex; flex-direction: column; justify-content: center;">
                        <div style="font-size: 24px; color: #64748b;">?</div>
                        <div style="font-weight: bold; color: #fff; font-size: 12px;">{char.name}</div>
                        <div style="font-size: 10px; color: #94a3b8;">Sin imagen</div>
                    </div>
                '''
        
        html += '</div>'
        return mark_safe(html)
    
    hero_carousel_preview.short_description = "Vista Previa (Imágenes Reales)"

@admin.register(ConnectionConfig)
class ConnectionConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_url', 'is_active')
    list_editable = ('is_active',)
    list_display_links = ('name',)

@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ('name', 'workflow_actions')
    
    def workflow_actions(self, obj):
        return format_html(
            '<a class="button" href="{}">Configurar</a>',
            reverse('admin:workflow_configure', args=[obj.pk])
        )
    workflow_actions.short_description = 'Acciones'
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
            self.message_user(request, f"Error al cargar el archivo JSON: {e}", level='error')
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
                if 'steps' in saved_config: workflow_params['steps'] = saved_config['steps']
                if 'cfg' in saved_config: workflow_params['cfg'] = saved_config['cfg']
                if 'sampler_name' in saved_config: workflow_params['sampler_name'] = saved_config['sampler_name']
                if 'scheduler' in saved_config: workflow_params['scheduler'] = saved_config['scheduler']
                
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
                'steps': request.POST.get('steps'),
                'cfg': request.POST.get('cfg'),
                'sampler_name': request.POST.get('sampler_name'),
                'scheduler': request.POST.get('scheduler'),
                'lora_names': request.POST.getlist('lora_name'),
                'lora_strengths': request.POST.getlist('lora_strength'),
                'prompt': request.POST.get('prompt'), 
            }
            new_config = {k: v for k, v in new_config.items() if v is not None}
            workflow.active_config = json.dumps(new_config)
            workflow.save()
            self.message_user(request, "Configuración guardada exitosamente.")
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
    list_display = ('image_preview', 'character', 'user', 'description')
    list_filter = ('character', 'user')
    search_fields = ('description', 'user__username', 'character__name')
    readonly_fields = ('image_preview', 'character', 'user', 'description', 'image')

    def image_preview(self, obj):
        if obj.image:
            url = reverse('serve_private_media', kwargs={'path': obj.image.name}) if obj.user else obj.image.url
            return format_html('<img src="{}" width="100" height="auto" />', url)
        return "(Sin imagen)"
    image_preview.short_description = 'Miniatura'
    def has_add_permission(self, request): return False
    # Habilitamos el borrado
    def has_delete_permission(self, request, obj=None): return True

class CharacterImageInline(admin.TabularInline):
    model = CharacterImage
    extra = 0 # No mostrar filas vacías extra
    fields = ('image_preview', 'user', 'description') # Mostrar explícitamente el usuario
    readonly_fields = ('image_preview', 'user', 'description') # Todo solo lectura
    
    # Habilitamos el borrado en el inline también
    def has_delete_permission(self, request, obj=None): return True

    def image_preview(self, obj):
        if obj.image: return format_html('<img src="{}" width="150" height="auto" />', obj.image.url)
        return "(Sin imagen)"
    image_preview.short_description = 'Vista Previa'

    def get_queryset(self, request):
        """
        Filtra las imágenes para mostrar SOLO las creadas por administradores (staff)
        o las que no tienen usuario asignado (legacy). Oculta las de clientes.
        """
        qs = super().get_queryset(request)
        # Mostrar si user es None O si el usuario es staff
        return qs.filter(Q(user__isnull=True) | Q(user__is_staff=True))

@admin.register(Character)
class CharacterAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_workflow', 'character_actions')
    inlines = [CharacterImageInline]
    
    # CAMBIO AQUÍ: Añadido 'description' al fieldset principal
    fieldsets = (
        (None, {'fields': ('name', 'description', 'base_workflow')}),
        ('Prompts por Defecto (Sándwich)', {
            'fields': ('prompt_prefix', 'positive_prompt', 'negative_prompt'),
            'description': 'Estructura: [Prefijo] + (Usuario:1.2) + [Sufijo]'
        }),
        ('Configuración Avanzada', {'classes': ('collapse',), 'fields': ('character_config',)}),
    )
    readonly_fields = ('character_config',)

    def character_actions(self, obj):
        return format_html(
            '<a class="button" href="{}">Configurar</a>',
            reverse('admin:character_configure', args=[obj.pk])
        )
    character_actions.short_description = 'Acciones'
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
            self.message_user(request, f"Error al cargar el archivo JSON del workflow base: {e}", level='error')
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
                if 'steps' in saved_config: workflow_params['steps'] = saved_config['steps']
                if 'cfg' in saved_config: workflow_params['cfg'] = saved_config['cfg']
                if 'sampler_name' in saved_config: workflow_params['sampler_name'] = saved_config['sampler_name']
                if 'scheduler' in saved_config: workflow_params['scheduler'] = saved_config['scheduler']
                
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
                'steps': request.POST.get('steps'),
                'cfg': request.POST.get('cfg'),
                'sampler_name': request.POST.get('sampler_name'),
                'scheduler': request.POST.get('scheduler'),
                'lora_names': request.POST.getlist('lora_name'),
                'lora_strengths': request.POST.getlist('lora_strength'),
                'prompt': request.POST.get('prompt'), 
            }
            new_config = {k: v for k, v in new_config.items() if v is not None}
            character.character_config = json.dumps(new_config)
            character.save()
            self.message_user(request, "Configuración del personaje guardada exitosamente.")
            return redirect('admin:myapp_character_changelist')

        context = {
            'character': character,
            'workflow': workflow,
            'workflow_params': workflow_params,
            'comfyui_info': comfyui_info,
            'saved_config': saved_config, 
            **self.admin_site.each_context(request), 
        }
        return render(request, 'admin/myapp/workflow/configure.html', context)

    def generate_character_image_view(self, request, character_id):
        character = get_object_or_404(Character, pk=character_id)
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        if request.method == 'POST':
            prompt = request.POST.get('prompt')
            if not character.character_config:
                msg = "El personaje no tiene configuración."
                if is_ajax: return JsonResponse({'status': 'error', 'message': msg})
                self.message_user(request, msg, level='error')
            else:
                try:
                    # USAMOS EL SERVICIO CENTRALIZADO
                    images_bytes_list, prompt_id = async_to_sync(generate_image_from_character)(character, prompt)
                    
                    if images_bytes_list:
                        count = 0
                        for i, img_bytes in enumerate(images_bytes_list):
                            # AHORA GUARDAMOS EL USUARIO (request.user)
                            new_image = CharacterImage(
                                character=character, 
                                description=prompt,
                                user=request.user # <-- AQUÍ ESTÁ EL CAMBIO IMPORTANTE
                            )
                            image_filename = f"generated_{character.name}_{prompt_id}_{i}.png"
                            new_image.image.save(image_filename, ContentFile(img_bytes), save=True)
                            count += 1
                        
                        if is_ajax: return JsonResponse({'status': 'success'})
                        self.message_user(request, f"{count} imágenes generadas y guardadas exitosamente.")
                    else:
                        msg = "La generación no produjo una imagen."
                        if is_ajax: return JsonResponse({'status': 'error', 'message': msg})
                        self.message_user(request, msg, level='warning')
                except Exception as e:
                    msg = f"Error durante la generación: {e}"
                    if is_ajax: return JsonResponse({'status': 'error', 'message': msg})
                    self.message_user(request, msg, level='error')
            
            # Si fue POST pero no AJAX (ej. form submit tradicional), redirigimos
            return redirect('admin:myapp_character_change', character_id)

        # Si es GET, redirigimos a la edición
        return redirect('admin:myapp_character_change', character_id)
