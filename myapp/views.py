from django.shortcuts import render, redirect, get_object_or_404
from django.db import models
from .models import Workflow, Character, CharacterImage, ConnectionConfig, CompanySettings, ChatMessage, CharacterCategory, CharacterSubCategory, ClientProfile, Coupon, CouponRedemption, CharacterAccessCode, UserCharacterAccess, TokenPackage, PaymentTransaction, SubscriptionPlan, UserSubscription
import json
import os
import uuid
from django.conf import settings
from asgiref.sync import sync_to_async
from django.http import JsonResponse, FileResponse, Http404
from django.core.files.base import ContentFile
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.urls import reverse
from django.db.models import Prefetch, Q, Count, Max
from django.contrib.auth.models import User
from .services import generate_image_from_character, get_active_comfyui_address, get_comfyui_object_info, analyze_workflow, update_workflow, queue_prompt, get_history, get_image, get_protocols, analyze_workflow_outputs
from PIL import Image as PILImage
import io
import httpx
import websockets
from django.core.cache import cache # IMPORTANTE: Para Rate Limiting Real
from allauth.socialaccount.models import SocialAccount # IMPORTANTE: Para verificar cuentas vinculadas
from allauth.account.models import EmailAddress # IMPORTANTE: Para limpiar emails antiguos
import random # IMPORTANTE: Para seleccionar imágenes aleatorias
from paypal.standard.forms import PayPalPaymentsForm # IMPORTANTE: Para PayPal
from django.views.decorators.csrf import csrf_exempt # IMPORTANTE: Para PayPal

# --- SECURE MEDIA SERVING VIEW ---
def serve_private_media(request, path):
    """
    Serves files from the 'user_images' folder.
    Allows access if:
    1. The requesting user is the owner.
    2. The requesting user is staff.
    3. The image owner is staff (public/official image).
    """
    # --- SECURITY FIX (Path Traversal) ---
    # Normalize the path to remove '..' and redundancies
    normalized_path = os.path.normpath(path)
    
    # Verify it doesn't try to exit the root directory
    if '..' in normalized_path or normalized_path.startswith(('/', '\\')):
        raise Http404("Invalid file path.")

    file_path = os.path.join(settings.MEDIA_ROOT, normalized_path)
    
    # Double check: ensure the final path is still within MEDIA_ROOT
    if not os.path.abspath(file_path).startswith(os.path.abspath(settings.MEDIA_ROOT)):
        raise Http404("Access denied: Path traversal attempt.")
    # ------------------------------------------------

    try:
        # Use normalized_path instead of raw path
        parts = normalized_path.split(os.sep) # Use system separator
        
        # Robust path handling (Windows/Linux)
        if len(parts) > 1 and parts[0] == 'user_images':
            owner_id = int(parts[1])
        else:
            # If not user_images, it could be another public or protected folder
            # By default, if it doesn't follow the user_images/ID/... pattern, deny access for now
            # unless it's staff.
            if request.user.is_staff:
                owner_id = request.user.id # Bypass for staff
            else:
                raise Http404("Not a user file.")

    except (ValueError, IndexError):
        raise Http404("Malformed file path.")

    # Check permissions
    has_access = False
    
    # 1. If the user is authenticated and is the owner or staff
    if request.user.is_authenticated:
        if request.user.id == owner_id or request.user.is_staff:
            has_access = True
    
    # 2. If no access yet, check if the image owner is staff (making it public)
    if not has_access:
        try:
            owner = User.objects.get(pk=owner_id)
            if owner.is_staff:
                has_access = True
        except User.DoesNotExist:
            pass

    if has_access:
        if os.path.exists(file_path):
            return FileResponse(open(file_path, 'rb'))
        else:
            raise Http404("File does not exist.")
    else:
        raise Http404("Access denied.")

# --- NEW SECURE FUNCTION TO GET CHARACTERS ---
@sync_to_async
def get_characters_with_images(user=None):
    # Base query: Active characters -> ORDERED BY SUBCATEGORY NAME, THEN CHARACTER NAME
    qs = Character.objects.filter(is_active=True).order_by('subcategory__name', 'name').prefetch_related('catalog_images_set').select_related('category', 'subcategory')
    
    if user and user.is_authenticated:
        # If user is logged in, show public OR private ones they have unlocked
        # Subquery for unlocked private characters
        unlocked_ids = UserCharacterAccess.objects.filter(user=user).values_list('character_id', flat=True)
        
        # Filter: (Public) OR (Private AND Unlocked)
        qs = qs.filter(Q(is_private=False) | Q(id__in=unlocked_ids))
    else:
        # If not logged in, only show public
        qs = qs.filter(is_private=False)
        
    return list(qs.all())

# --- FUNCTION TO GET COMPANY SETTINGS ---
@sync_to_async
def get_company_settings():
    # Prefetch to get hero carousel images
    return CompanySettings.objects.prefetch_related('hero_images').first()

# --- HELPER FUNCTION TO GET USER SAFELY ---
@sync_to_async
def get_user_from_request(request):
    user = request.user
    if user.is_authenticated:
        pass 
    return user

# --- PROFILE VIEW ---
async def profile_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return redirect('account_login')
    
    company_settings = await get_company_settings()
    
    # Get profile data (tokens, etc.)
    try:
        profile = await sync_to_async(lambda: user.clientprofile)()
        tokens = await profile.get_tokens_remaining_async()
    except ClientProfile.DoesNotExist:
        tokens = 0
        
    # Get stats (e.g., total images generated)
    total_images = await sync_to_async(CharacterImage.objects.filter(user=user).count)()

    # --- NEW: Check Social Accounts (Google) ---
    # Obtenemos TODAS las cuentas de Google ordenadas por last_login (la más reciente primero)
    google_accounts = await sync_to_async(list)(
        SocialAccount.objects.filter(user=user, provider='google').order_by('-last_login')
    )
    
    active_google_account = None

    # --- LOGICA DE SUSTITUCIÓN DE CUENTA ---
    if google_accounts:
        latest_account = google_accounts[0] # La que acabamos de conectar/usar
        active_google_account = latest_account
        
        # 1. Actualizar email del usuario si es diferente
        google_email = latest_account.extra_data.get('email')
        if google_email and google_email != user.email:
            # Guardamos el email antiguo para borrarlo de Allauth después
            old_email = user.email
            
            user.email = google_email
            
            # --- NUEVO: Actualizar Username Automáticamente ---
            base_username = google_email.split('@')[0]
            new_username = base_username
            
            @sync_to_async
            def check_username_exists(uname):
                return User.objects.filter(username=uname).exclude(pk=user.pk).exists()
            
            counter = 1
            while await check_username_exists(new_username):
                new_username = f"{base_username}{counter}"
                counter += 1
            
            user.username = new_username
            # --------------------------------------------------

            await sync_to_async(user.save)()
            
            # --- LIMPIEZA PROFUNDA DE EMAILS (ALLAUTH) ---
            # Borramos el email antiguo de la tabla de EmailAddress para liberar la cuenta
            if old_email:
                @sync_to_async
                def clean_old_email_address(email_to_remove):
                    EmailAddress.objects.filter(email=email_to_remove).delete()
                await clean_old_email_address(old_email)
            
            # Creamos/Actualizamos el nuevo email en EmailAddress como verificado y primario
            @sync_to_async
            def update_new_email_address(user_obj, new_email):
                # Borramos cualquier registro previo de este nuevo email (por si acaso)
                EmailAddress.objects.filter(email=new_email).delete()
                # Creamos el nuevo registro limpio
                EmailAddress.objects.create(
                    user=user_obj,
                    email=new_email,
                    verified=True,
                    primary=True
                )
            await update_new_email_address(user, google_email)
            # ---------------------------------------------
            
        # 2. Eliminar cuentas antiguas (si hay más de una)
        if len(google_accounts) > 1:
            # Definimos una función síncrona para borrar
            @sync_to_async
            def delete_old_accounts(accounts_list, keep_id):
                for acc in accounts_list:
                    if acc.id != keep_id:
                        acc.delete()
            
            # Ejecutamos el borrado de las antiguas
            await delete_old_accounts(google_accounts, latest_account.id)
    
    # --- LOGICA DE PLAN DE SUSCRIPCIÓN ---
    plan_name = "Free Plan"
    is_subscribed = False
    try:
        # Accedemos a la suscripción (OneToOne) de forma segura en async
        @sync_to_async
        def get_subscription_info(u):
            try:
                sub = u.subscription
                if sub.status == 'ACTIVE' and sub.plan:
                    return sub.plan.name, True
            except UserSubscription.DoesNotExist:
                pass
            return "Free Plan", False

        plan_name, is_subscribed = await get_subscription_info(user)
        
    except Exception:
        pass

    context = {
        'company': company_settings,
        'tokens': tokens,
        'total_images': total_images,
        'google_account': active_google_account, # Pasamos UN SOLO objeto (o None)
        'is_google_linked': active_google_account is not None,
        'plan_name': plan_name, # NUEVO
        'is_subscribed': is_subscribed # NUEVO
    }
    return await sync_to_async(render)(request, 'myapp/profile.html', context)

# --- UPDATE USERNAME VIEW ---
async def update_username_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
    
    if request.method == 'POST':
        new_username = request.POST.get('username')
        if not new_username:
            return JsonResponse({'status': 'error', 'message': 'Username cannot be empty'})
        
        # Basic validation
        if len(new_username) < 3:
            return JsonResponse({'status': 'error', 'message': 'Username must be at least 3 characters long'})
            
        try:
            @sync_to_async
            def perform_update(user_obj, username):
                # Check if username exists (excluding current user)
                if User.objects.filter(username=username).exclude(pk=user_obj.pk).exists():
                    return {"success": False, "message": "Username already taken."}
                
                user_obj.username = username
                user_obj.save()
                return {"success": True}

            result = await perform_update(user, new_username)
            
            if result["success"]:
                return JsonResponse({'status': 'success', 'message': 'Username updated successfully'})
            else:
                return JsonResponse({'status': 'error', 'message': result["message"]})

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
            
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

# --- GALLERY VIEW ---
async def gallery_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return redirect('account_login')
    
    company_settings = await get_company_settings()
    
    # Get all images generated by the user
    user_images = await sync_to_async(list)(
        CharacterImage.objects.filter(user=user).select_related('character', 'character__category', 'character__subcategory').order_by('-id')
    )
    
    # --- NEW: Get all categories and subcategories ORDERED BY NAME ---
    all_categories = await sync_to_async(list)(CharacterCategory.objects.all().order_by('name'))
    all_subcategories = await sync_to_async(list)(CharacterSubCategory.objects.all().order_by('name'))
    
    # Group by character
    public_gallery = {}
    private_gallery = {}
    
    for img in user_images:
        char_id = img.character.id
        is_private = img.character.is_private
        
        target_dict = private_gallery if is_private else public_gallery
        
        if char_id not in target_dict:
            target_dict[char_id] = {
                'character': img.character,
                'images': [],
                'count': 0,
                'latest_image': img
            }
        target_dict[char_id]['images'].append({
            'id': img.id,
            'url': img.image.url
        })
        target_dict[char_id]['count'] += 1
    
    context = {
        'company': company_settings,
        'public_gallery': list(public_gallery.values()),
        'private_gallery': list(private_gallery.values()),
        'all_categories': all_categories, # Pass categories to template
        'all_subcategories': all_subcategories, # Pass subcategories to template
    }
    return await sync_to_async(render)(request, 'myapp/gallery.html', context)

# --- VIEW TO DELETE IMAGES ---
async def delete_images_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
    
    if request.method == 'POST':
        try:
            image_ids = request.POST.getlist('image_ids[]')
            if not image_ids:
                return JsonResponse({'status': 'error', 'message': 'No images selected'})
            
            @sync_to_async
            def perform_delete(ids, user_obj):
                qs = CharacterImage.objects.filter(id__in=ids, user=user_obj)
                deleted_count = 0
                for img in qs:
                    if img.image:
                        img.image.delete(save=False)
                    deleted_count += 1
                qs.delete()
                return deleted_count

            count = await perform_delete(image_ids, user)
            return JsonResponse({'status': 'success', 'deleted_count': count})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

# --- VIEW TO DELETE INDIVIDUAL MESSAGE ---
async def delete_message_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
    
    if request.method == 'POST':
        try:
            message_id = request.POST.get('message_id')
            delete_images = request.POST.get('delete_images') == 'true'
            
            @sync_to_async
            def perform_message_delete(msg_id, user_obj, del_imgs):
                try:
                    msg = ChatMessage.objects.get(id=msg_id, user=user_obj)
                    
                    if del_imgs:
                        # Delete associated images
                        images = msg.generated_images.all()
                        for img in images:
                            if img.image:
                                img.image.delete(save=False) # Delete file
                            img.delete() # Delete record
                    
                    msg.delete()
                    return True
                except ChatMessage.DoesNotExist:
                    return False

            success = await perform_message_delete(message_id, user, delete_images)
            
            if success:
                return JsonResponse({'status': 'success'})
            else:
                return JsonResponse({'status': 'error', 'message': 'Message not found'})
                
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
            
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

# --- VIEW TO CLEAR ENTIRE CHAT HISTORY ---
async def clear_chat_history_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
    
    if request.method == 'POST':
        try:
            character_id = request.POST.get('character_id')
            delete_images = request.POST.get('delete_images') == 'true'
            
            @sync_to_async
            def perform_clear_chat(char_id, user_obj, del_imgs):
                # Get all messages for this chat
                msgs = ChatMessage.objects.filter(user=user_obj, character_id=char_id)
                
                if del_imgs:
                    # Collect all images from these messages
                    for msg in msgs:
                        images = msg.generated_images.all()
                        for img in images:
                            if img.image:
                                img.image.delete(save=False)
                            img.delete()
                
                # Delete the messages
                count, _ = msgs.delete()
                return count

            deleted_count = await perform_clear_chat(character_id, user, delete_images)
            return JsonResponse({'status': 'success', 'deleted_count': deleted_count})
                
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
            
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

# --- WORKSPACE VIEW ---
async def workspace_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return redirect('account_login')
    
    company_settings = await get_company_settings()
    
    # Get all characters for the selection modal (Filtered by user access)
    all_characters = await get_characters_with_images(user)
    
    # --- NEW: Get all categories and subcategories ORDERED BY NAME ---
    all_categories = await sync_to_async(list)(CharacterCategory.objects.all().order_by('name'))
    all_subcategories = await sync_to_async(list)(CharacterSubCategory.objects.all().order_by('name'))
    
    # Check if a character is selected
    character_id = request.GET.get('character_id')
    
    # CHANGE: Always load history if a character is selected
    should_load_history = True
    
    selected_character = None
    chat_history = [] # List for the central chat
    recent_chats = [] # List for the left sidebar
    
    # Default values if no character
    default_width = 1024
    default_height = 1024
    default_seed = -1 # -1 means random
    
    # Workflow capabilities (to show/hide checkboxes)
    workflow_capabilities = {
        'can_upscale': False,
        'can_facedetail': False,
        'can_eyedetailer': False # NUEVO: Por defecto False
    }

    # --- NEW: Get list of recent chats WITH IMAGES ---
    @sync_to_async
    def get_recent_chats_list():
        # 1. Get character IDs spoken to, ordered by date
        recent_ids = list(ChatMessage.objects.filter(user=user)
                          .values_list('character_id', flat=True)
                          .order_by('-timestamp'))
        
        # 2. Remove duplicates while maintaining order
        seen = set()
        unique_ids = [x for x in recent_ids if not (x in seen or seen.add(x))]
        
        if not unique_ids:
            return []

        # 3. Get the full Character objects (with catalog images)
        # --- NEW: Filter by is_active=True ---
        chars_qs = Character.objects.filter(id__in=unique_ids, is_active=True).prefetch_related('catalog_images_set')
        chars_dict = {c.id: c for c in chars_qs}
        
        # 4. Rebuild the list in the correct order
        ordered_chars = []
        for cid in unique_ids:
            if cid in chars_dict:
                ordered_chars.append(chars_dict[cid])
                
        return ordered_chars

    recent_chats = await get_recent_chats_list()

    # --- FIXED: Random Preview Images for Welcome Screen ---
    random_preview_images = []
    if not character_id:
        # 1. Get all active characters with their catalog images prefetched
        # --- CHANGE: Use the filtered list we already got ---
        all_active_chars = all_characters
        
        # 2. Collect all catalog images into a single list
        all_catalog_images = []
        for char in all_active_chars:
            all_catalog_images.extend(list(char.catalog_images_set.all()))

        # 3. Shuffle and pick 2 if available
        if len(all_catalog_images) >= 2:
            random_images = random.sample(all_catalog_images, 2)
        else:
            random_images = all_catalog_images # Take all if less than 2
            
        # 4. Format for the template
        for img in random_images:
            random_preview_images.append({
                'character_id': img.character.id, # An image object should have a character attribute
                'image_url': img.image.url
            })

    if character_id:
        try:
            # Search in the already loaded list to avoid another query
            selected_character = next((c for c in all_characters if str(c.id) == str(character_id)), None)

            # --- NEW: Analyze Workflow to determine capabilities ---
            if selected_character:
                # --- NEW: Extract default dimensions and seed from character's JSON ---
                if selected_character.character_config:
                    try:
                        config = json.loads(selected_character.character_config)
                        if 'width' in config: default_width = int(config['width'])
                        if 'height' in config: default_height = int(config['height'])
                        
                        # Seed logic:
                        if config.get('seed_behavior') == 'fixed' and 'seed' in config:
                            default_seed = int(config['seed'])
                        else:
                            default_seed = -1
                            
                    except (json.JSONDecodeError, ValueError):
                        pass 

                @sync_to_async
                def get_workflow_json():
                    with open(selected_character.base_workflow.json_file.path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                
                try:
                    wf_json = await get_workflow_json()
                    # analyze_workflow_outputs needs the node structure, so we use the base.
                    workflow_capabilities = analyze_workflow_outputs(wf_json)
                except Exception as e:
                    print(f"Error analyzing workflow: {e}")

            # --- CHANGE: Load History ONLY IF REQUESTED ---
            if selected_character and should_load_history:
                chat_qs = await sync_to_async(list)(
                    ChatMessage.objects.filter(
                        user=user, 
                        character=selected_character
                    ).prefetch_related('generated_images').order_by('timestamp')
                )
                
                # Format for the template
                for msg in chat_qs:
                    item = {
                        'id': msg.id, # Needed for deletion
                        'is_user': msg.is_from_user,
                        'text': msg.message,
                        'images': []
                    }
                    if not msg.is_from_user:
                        # Get associated images
                        imgs = await sync_to_async(list)(msg.generated_images.all())
                        
                        # --- PLACEHOLDER LOGIC ---
                        real_images_count = len(imgs)
                        expected_count = msg.image_count
                        
                        # First, add the real images
                        for img in imgs:
                            # --- CORRECCIÓN: Usar el campo de la BD en lugar de adivinar por nombre ---
                            img_type = "NORMAL"
                            if img.generation_type == "Gen_UpScaler": img_type = "UPSCALER"
                            elif img.generation_type == "Gen_FaceDetailer": img_type = "FACEDETAILER"
                            elif img.generation_type == "Gen_EyeDetailer": img_type = "EYEDETAILER"
                            
                            item['images'].append({
                                'url': img.image.url,
                                'type': img_type,
                                'width': img.width, # Pass dimensions
                                'height': img.height,
                                'is_deleted': False
                            })
                        
                        # Then fill with placeholders if any are missing
                        if expected_count > real_images_count:
                            missing_count = expected_count - real_images_count
                            for _ in range(missing_count):
                                item['images'].append({
                                    'url': None,
                                    'type': "DELETED",
                                    'is_deleted': True
                                })
                                
                    chat_history.append(item)
                    
        except Exception:
            pass

    context = {
        'company': company_settings,
        'selected_character': selected_character,
        'all_characters': all_characters,
        'all_categories': all_categories, # Pass categories to template
        'all_subcategories': all_subcategories, # Pass subcategories to template
        'default_width': default_width, # Pass to context
        'default_height': default_height, # Pass to context
        'default_seed': default_seed, # Pass seed to context
        'chat_history': chat_history, # Pass chat history
        'recent_chats': recent_chats, # Pass recent chats list
        'workflow_capabilities': workflow_capabilities, # Pass capabilities
        'random_preview_images': random_preview_images # Pass random images
    }
    return await sync_to_async(render)(request, 'myapp/workspace.html', context)

# --- GENERATE IMAGE VIEW (RESTORED) ---
async def generate_image_view(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    user = await get_user_from_request(request)

    if is_ajax:
        if request.method == 'POST':
            if not user.is_authenticated:
                return JsonResponse({'status': 'error', 'message': 'You must be logged in to generate images.'}, status=401)
            
            # --- TOKEN VALIDATION (NEW) ---
            # Only if not staff (admins have infinite tokens)
            if not user.is_staff:
                try:
                    profile = await sync_to_async(lambda: user.clientprofile)()
                    await profile.check_and_reset_tokens()
                    
                    # CHANGE: Use async method
                    tokens_left = await profile.get_tokens_remaining_async()
                    if tokens_left <= 0:
                        return JsonResponse({'status': 'error', 'message': 'You have run out of tokens. Please contact support or wait for your next reset.'}, status=403)
                except ClientProfile.DoesNotExist:
                    # If no profile, create a default one (fallback)
                    await sync_to_async(ClientProfile.objects.create)(user=user)
            # ------------------------------------

            # --- REAL RATE LIMITING (CACHE) ---
            # Use user ID as key, not session.
            # This prevents clearing cookies to bypass the limit.
            cache_key = f"gen_limit_{user.id}"
            
            # Check if key exists in cache
            if cache.get(cache_key):
                ttl = cache.ttl(cache_key) # Remaining time
                return JsonResponse({'status': 'error', 'message': f'Please wait {ttl} seconds before generating another image.'}, status=429)
            
            # Set the lock for 10 seconds
            cache.set(cache_key, True, timeout=10)
            # ----------------------------------

            character_id = request.POST.get('character_id')
            user_prompt = request.POST.get('prompt')
            
            if len(user_prompt) > 5000:
                return JsonResponse({'status': 'error', 'message': 'Prompt is too long (max 5000 characters).'}, status=400)
            
            width = request.POST.get('width')
            height = request.POST.get('height')
            seed = request.POST.get('seed')
            
            # --- SINGLE SELECTION LOGIC ---
            generation_type = request.POST.get('generation_type', 'Gen_Normal')
            
            # Validate type
            valid_types = ["Gen_Normal", "Gen_UpScaler", "Gen_FaceDetailer", "Gen_EyeDetailer"]
            if generation_type not in valid_types:
                generation_type = "Gen_Normal"
                
            allowed_types = [generation_type] # Pass as a list with one item
            # ------------------------------

            try:
                character = await sync_to_async(Character.objects.select_related('base_workflow').get)(id=character_id)

                # --- NEW: PRIVATE CHARACTER QUOTA CHECK ---
                if character.is_private and not user.is_staff:
                    try:
                        access = await sync_to_async(UserCharacterAccess.objects.get)(user=user, character=character)
                        await access.check_and_reset_quota()
                        
                        if access.remaining_generations <= 0:
                            return JsonResponse({'status': 'error', 'message': 'You have reached the generation limit for this private character.'}, status=403)
                    except UserCharacterAccess.DoesNotExist:
                        return JsonResponse({'status': 'error', 'message': 'You do not have access to this private character.'}, status=403)
                # ------------------------------------------

                @sync_to_async
                def save_user_message():
                    return ChatMessage.objects.create(user=user, character=character, message=user_prompt, is_from_user=True)
                user_msg = await save_user_message()
                
                images_data_list, prompt_id, final_workflow_json = await generate_image_from_character(
                    character, user_prompt, width, height, seed=seed, allowed_types=allowed_types
                )

                if images_data_list:
                    # --- DEDUCT TOKEN (GLOBAL) ---
                    if not user.is_staff:
                        @sync_to_async
                        def deduct_token(u):
                            p = u.clientprofile
                            p.tokens_used += 1
                            p.save()
                        await deduct_token(user)
                        
                        # --- DEDUCT PRIVATE QUOTA ---
                        if character.is_private:
                            @sync_to_async
                            def deduct_private_quota(u, c):
                                try:
                                    acc = UserCharacterAccess.objects.get(user=u, character=c)
                                    acc.images_generated_current_period += 1
                                    acc.save()
                                except UserCharacterAccess.DoesNotExist:
                                    pass
                            await deduct_private_quota(user, character)
                    # -------------------------------

                    generated_results = []
                    created_images = []
                    
                    @sync_to_async
                    def save_generated_image(img_bytes, classification, index, workflow_json):
                        # CHANGE: Save generation type AND workflow
                        new_image = CharacterImage(
                            character=character, 
                            user=user, 
                            description=user_prompt,
                            generation_type=classification # Save type here
                        )
                        
                        # Save workflow file
                        workflow_filename = f"workflow_{character.name}_{prompt_id}_{classification}_{index}.json"
                        new_image.generation_workflow.save(workflow_filename, ContentFile(json.dumps(workflow_json, indent=2).encode('utf-8')), save=False)
                        
                        filename = f"user_gen_{character.name}_{prompt_id}_{classification}_{index}.png"
                        new_image.image.save(filename, ContentFile(img_bytes), save=False)
                        try:
                            with PILImage.open(io.BytesIO(img_bytes)) as pil_img:
                                new_image.width, new_image.height = pil_img.size
                        except Exception:
                            pass
                        new_image.save()
                        return new_image

                    for i, (img_bytes, classification) in enumerate(images_data_list):
                        img_obj = await save_generated_image(img_bytes, classification, i, final_workflow_json)
                        created_images.append(img_obj)
                        image_url = reverse('serve_private_media', kwargs={'path': img_obj.image.name})
                        generated_results.append({'url': image_url, 'type': classification, 'width': img_obj.width, 'height': img_obj.height})
                    
                    @sync_to_async
                    def save_ai_message(imgs):
                        ai_msg = ChatMessage.objects.create(user=user, character=character, message="Here are your generated images.", is_from_user=False, image_count=len(imgs))
                        ai_msg.generated_images.set(imgs)
                        return ai_msg
                    
                    ai_msg = await save_ai_message(created_images)
                    
                    return JsonResponse({'status': 'success', 'results': generated_results, 'user_msg_id': user_msg.id, 'ai_msg_id': ai_msg.id})
                
                return JsonResponse({'status': 'error', 'message': 'No valid images generated based on your filters.'}, status=500)

            except Character.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Character not found.'}, status=404)
            except (httpx.ConnectError, websockets.exceptions.WebSocketException):
                # Capture specific connection errors
                return JsonResponse({'status': 'error', 'message': 'Could not connect to the generation server. Please try again later.'}, status=503)
            except Exception as e:
                # For any other error, log the real error on the server console
                print(f"An unexpected error occurred: {e}")
                # And show a generic message to the user
                return JsonResponse({'status': 'error', 'message': 'An unexpected error occurred during generation.'}, status=500)

        elif request.method == 'GET':
            if not user.is_authenticated:
                 return JsonResponse({'status': 'success', 'images': []})

            character_id = request.GET.get('character_id')
            try:
                images_qs = await sync_to_async(list)(
                    CharacterImage.objects.filter(character_id=character_id, user=user).values_list('image', flat=True).order_by('-id')
                )
                image_urls = [reverse('serve_private_media', kwargs={'path': name}) for name in images_qs]
                return JsonResponse({'status': 'success', 'images': image_urls})
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    if request.method == 'GET':
        characters = await get_characters_with_images(user)
        company_settings = await get_company_settings()
        
        # --- FIX: Add loading of categories and subcategories ORDERED BY NAME ---
        all_categories = await sync_to_async(list)(CharacterCategory.objects.all().order_by('name'))
        all_subcategories = await sync_to_async(list)(CharacterSubCategory.objects.all().order_by('name'))

        # --- HERO CAROUSEL LOGIC (NEW) ---
        hero_items = []
        if company_settings:
            # Get carousel images directly from the HeroCarouselImage model
            hero_images = await sync_to_async(list)(company_settings.hero_images.all())
            
            for img in hero_images:
                hero_items.append({
                    'image_url': img.image.url,
                    'name': img.caption or "" # Use caption or empty
                })
        
        context = {
            'characters': characters,
            'company': company_settings,
            'hero_items': hero_items,
            'all_categories': all_categories, # Pass categories to template
            'all_subcategories': all_subcategories, # Pass subcategories to template
        }
        return await sync_to_async(render)(request, 'myapp/generate.html', context)
    
    return redirect('generate_image')

async def redeem_coupon_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return JsonResponse({'status': 'error', 'message': 'Unauthorized'}, status=401)
    
    if request.method == 'POST':
        code = request.POST.get('code')
        if not code:
            return JsonResponse({'status': 'error', 'message': 'Code is required'})
        
        try:
            @sync_to_async
            def process_redemption(user_obj, input_code):
                # 1. Try to redeem as Token Coupon
                try:
                    coupon = Coupon.objects.get(code=input_code) # Removed is_redeemed=False check here
                    
                    # Check if user already redeemed this coupon
                    if CouponRedemption.objects.filter(user=user_obj, coupon=coupon).exists():
                        return {"success": False, "message": "You have already redeemed this coupon."}
                    
                    # Check global limit
                    if coupon.max_redemptions is not None and coupon.times_redeemed >= coupon.max_redemptions:
                        return {"success": False, "message": "This coupon has reached its maximum usage limit."}
                    
                    # Process redemption
                    CouponRedemption.objects.create(user=user_obj, coupon=coupon)
                    
                    # Update coupon stats
                    coupon.times_redeemed += 1
                    # Optional: Mark as fully redeemed if limit reached (for visual purposes in admin)
                    if coupon.max_redemptions and coupon.times_redeemed >= coupon.max_redemptions:
                        coupon.is_redeemed = True 
                    coupon.save()
                    
                    # Grant tokens
                    profile, _ = ClientProfile.objects.get_or_create(user=user_obj)
                    profile.bonus_tokens += coupon.tokens
                    profile.save()
                    
                    return {"success": True, "message": f"Successfully redeemed {coupon.tokens} tokens!"}
                except Coupon.DoesNotExist:
                    pass # Continue to check Character Codes

                # 2. Try to redeem as Character Access Code
                try:
                    char_code = CharacterAccessCode.objects.get(code=input_code, is_active=True)
                    
                    # --- NEW: Check Global Limit ---
                    if char_code.max_redemptions is not None and char_code.times_redeemed >= char_code.max_redemptions:
                        return {"success": False, "message": "This code has reached its maximum usage limit."}
                    
                    # Check if user already has this character
                    if UserCharacterAccess.objects.filter(user=user_obj, character=char_code.character).exists():
                        return {"success": False, "message": "You already have access to this character."}
                    
                    # Create access record
                    UserCharacterAccess.objects.create(
                        user=user_obj,
                        character=char_code.character,
                        source_code=char_code,
                        limit_amount=char_code.limit_amount,
                        reset_interval=char_code.reset_interval
                    )
                    
                    # Increment counter
                    char_code.times_redeemed += 1
                    char_code.save()
                    
                    return {"success": True, "message": f"Successfully unlocked character: {char_code.character.name}!"}
                    
                except CharacterAccessCode.DoesNotExist:
                    return {"success": False, "message": "Invalid code."}

            result = await process_redemption(user, code)
            
            if result["success"]:
                return JsonResponse({'status': 'success', 'message': result["message"]})
            else:
                return JsonResponse({'status': 'error', 'message': result["message"]})

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
            
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

# --- PAYPAL VIEWS ---

@login_required
def token_packages(request):
    company_settings = CompanySettings.objects.first()
    
    # --- NEW: Check if token sales are active ---
    if company_settings and not company_settings.is_token_sale_active:
        return redirect('profile') # Redirect to profile if disabled
    
    packages = TokenPackage.objects.filter(is_active=True)
    return render(request, 'myapp/token_packages.html', {'packages': packages, 'company': company_settings})

@login_required
def payment_process(request, package_id):
    company_settings = CompanySettings.objects.first()
    
    # --- NEW: Check if token sales are active ---
    if company_settings and not company_settings.is_token_sale_active:
        return redirect('profile')

    package = get_object_or_404(TokenPackage, id=package_id)
    host = request.get_host()
    
    # Crear transacción
    transaction = PaymentTransaction.objects.create(
        user=request.user,
        package=package,
        amount=package.price
    )
    
    # --- CONFIGURACIÓN DINÁMICA DE PAYPAL DESDE BD ---
    receiver_email = company_settings.paypal_receiver_email if company_settings.paypal_receiver_email else settings.PAYPAL_RECEIVER_EMAIL
    
    paypal_dict = {
        'business': receiver_email,
        'amount': str(package.price),
        'item_name': package.name,
        'invoice': str(transaction.id),
        'currency_code': 'USD',
        'notify_url': f'http://{host}{reverse("paypal-ipn")}',
        'return_url': f'http://{host}{reverse("payment_done")}',
        'cancel_return': f'http://{host}{reverse("payment_canceled")}',
        'custom': str(transaction.id), # Pasamos el ID de la transacción para recuperarlo en la señal
    }
    
    # Instanciar formulario
    form = PayPalPaymentsForm(initial=paypal_dict)
    
    # --- MONKEY PATCH PARA CAMBIAR ENDPOINT DINÁMICAMENTE ---
    # Esto fuerza la URL de acción correcta basada en la configuración de la BD
    if company_settings.paypal_is_sandbox:
        form.get_endpoint = lambda: "https://www.sandbox.paypal.com/cgi-bin/webscr"
    else:
        form.get_endpoint = lambda: "https://www.paypal.com/cgi-bin/webscr"
    
    return render(request, 'myapp/payment_process.html', {
        'form': form, 
        'package': package,
        'company': company_settings
    })

@csrf_exempt
def payment_done(request):
    company_settings = CompanySettings.objects.first()
    return render(request, 'myapp/payment_done.html', {'company': company_settings})

@csrf_exempt
def payment_canceled(request):
    company_settings = CompanySettings.objects.first()
    return render(request, 'myapp/payment_canceled.html', {'company': company_settings})

# --- SUBSCRIPTION VIEWS ---

@login_required
def subscription_plans(request):
    company_settings = CompanySettings.objects.first()
    
    # --- NEW: Check if subscriptions are active ---
    if company_settings and not company_settings.is_subscription_active:
        return redirect('profile')
        
    plans = SubscriptionPlan.objects.filter(is_active=True)
    
    # Check current subscription
    current_sub = None
    try:
        current_sub = request.user.subscription
    except UserSubscription.DoesNotExist:
        pass
        
    return render(request, 'myapp/subscription_plans.html', {
        'plans': plans, 
        'company': company_settings,
        'current_sub': current_sub
    })

@login_required
def subscription_process(request, plan_id):
    company_settings = CompanySettings.objects.first()
    
    # --- NEW: Check if subscriptions are active ---
    if company_settings and not company_settings.is_subscription_active:
        return redirect('profile')

    plan = get_object_or_404(SubscriptionPlan, id=plan_id)
    host = request.get_host()
    
    # Create or update pending subscription record
    sub, created = UserSubscription.objects.get_or_create(user=request.user)
    sub.plan = plan
    sub.status = 'PENDING'
    sub.save()
    
    # --- CONFIGURACIÓN DINÁMICA DE PAYPAL DESDE BD ---
    receiver_email = company_settings.paypal_receiver_email if company_settings.paypal_receiver_email else settings.PAYPAL_RECEIVER_EMAIL

    # PayPal Subscription Parameters
    paypal_dict = {
        'cmd': '_xclick-subscriptions',
        'business': receiver_email,
        'a3': str(plan.price), # Regular subscription price
        'p3': plan.billing_period, # Subscription duration
        't3': plan.billing_period_unit, # Subscription duration unit (D, W, M, Y)
        'src': '1', # Recurring payments
        'sra': '1', # Reattempt on failure
        'item_name': plan.name,
        'invoice': str(uuid.uuid4()), # Unique invoice ID
        'currency_code': 'USD',
        'notify_url': f'http://{host}{reverse("paypal-ipn")}',
        'return_url': f'http://{host}{reverse("subscription_done")}',
        'cancel_return': f'http://{host}{reverse("subscription_canceled")}',
        'custom': str(request.user.id), # Pass User ID to identify who is subscribing
    }
    
    form = PayPalPaymentsForm(initial=paypal_dict)
    
    # --- MONKEY PATCH PARA CAMBIAR ENDPOINT DINÁMICAMENTE ---
    if company_settings.paypal_is_sandbox:
        form.get_endpoint = lambda: "https://www.sandbox.paypal.com/cgi-bin/webscr"
    else:
        form.get_endpoint = lambda: "https://www.paypal.com/cgi-bin/webscr"
    
    return render(request, 'myapp/subscription_process.html', {
        'form': form, 
        'plan': plan,
        'company': company_settings
    })

@csrf_exempt
def subscription_done(request):
    company_settings = CompanySettings.objects.first()
    return render(request, 'myapp/subscription_done.html', {'company': company_settings})

@csrf_exempt
def subscription_canceled(request):
    company_settings = CompanySettings.objects.first()
    return render(request, 'myapp/subscription_canceled.html', {'company': company_settings})
