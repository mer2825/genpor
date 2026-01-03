from django.shortcuts import render, redirect, get_object_or_404
from django.db import models
from .models import Workflow, Character, CharacterImage, ConnectionConfig, CompanySettings, ChatMessage
import json
import os
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

# --- VISTA SEGURA PARA SERVIR ARCHIVOS ---
def serve_private_media(request, path):
    """
    Sirve archivos de la carpeta 'user_images'.
    Permite acceso si:
    1. El usuario solicitante es el dueño.
    2. El usuario solicitante es staff.
    3. El dueño de la imagen es staff (imagen pública/oficial).
    """
    # --- CORRECCIÓN DE SEGURIDAD (Path Traversal) ---
    # Normalizar la ruta para eliminar '..' y redundancias
    normalized_path = os.path.normpath(path)
    
    # Verificar que no intente salir del directorio raíz
    if '..' in normalized_path or normalized_path.startswith(('/', '\\')):
        raise Http404("Invalid file path.")

    file_path = os.path.join(settings.MEDIA_ROOT, normalized_path)
    
    # Doble verificación: asegurar que la ruta final sigue estando dentro de MEDIA_ROOT
    if not os.path.abspath(file_path).startswith(os.path.abspath(settings.MEDIA_ROOT)):
        raise Http404("Access denied: Path traversal attempt.")
    # ------------------------------------------------

    try:
        # Usamos normalized_path en lugar de path crudo
        parts = normalized_path.split(os.sep) # Usar separador del sistema
        
        # Manejo robusto de rutas (Windows/Linux)
        if len(parts) > 1 and parts[0] == 'user_images':
            owner_id = int(parts[1])
        else:
            # Si no es user_images, podría ser otra carpeta pública o protegida
            # Por defecto, si no sigue el patrón user_images/ID/..., denegamos acceso por ahora
            # a menos que sea staff.
            if request.user.is_staff:
                owner_id = request.user.id # Bypass para staff
            else:
                raise Http404("Not a user file.")

    except (ValueError, IndexError):
        raise Http404("Malformed file path.")

    # Verificar permisos
    has_access = False
    
    # 1. Si el usuario está autenticado y es dueño o staff
    if request.user.is_authenticated:
        if request.user.id == owner_id or request.user.is_staff:
            has_access = True
    
    # 2. Si no tiene acceso aún, verificar si el dueño de la imagen es staff (hacerla pública)
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

# --- NUEVA FUNCIÓN SEGURA PARA OBTENER PERSONAJES ---
@sync_to_async
def get_characters_with_images():
    # CAMBIO: Ahora usamos 'catalog_images_set' para las imágenes públicas
    return list(Character.objects.prefetch_related('catalog_images_set').all())

# --- FUNCIÓN PARA OBTENER LA CONFIGURACIÓN DE LA EMPRESA ---
@sync_to_async
def get_company_settings():
    # Prefetch para obtener las imágenes del carrusel hero
    return CompanySettings.objects.prefetch_related('hero_images').first()

# --- FUNCIÓN AUXILIAR PARA OBTENER EL USUARIO DE FORMA SEGURA ---
@sync_to_async
def get_user_from_request(request):
    user = request.user
    if user.is_authenticated:
        pass 
    return user

# --- VISTA DE GALERÍA ---
async def gallery_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return redirect('account_login')
    
    company_settings = await get_company_settings()
    
    # Obtener todas las imágenes generadas por el usuario
    user_images = await sync_to_async(list)(
        CharacterImage.objects.filter(user=user).select_related('character').order_by('-id')
    )
    
    # Agrupar por personaje
    gallery_data = {}
    for img in user_images:
        char_id = img.character.id
        if char_id not in gallery_data:
            gallery_data[char_id] = {
                'character': img.character,
                'images': [],
                'count': 0,
                'latest_image': img
            }
        gallery_data[char_id]['images'].append({
            'id': img.id,
            'url': img.image.url
        })
        gallery_data[char_id]['count'] += 1
    
    context = {
        'company': company_settings,
        'gallery_data': list(gallery_data.values())
    }
    return await sync_to_async(render)(request, 'myapp/gallery.html', context)

# --- VISTA PARA ELIMINAR IMÁGENES ---
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

# --- VISTA PARA ELIMINAR MENSAJE INDIVIDUAL ---
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
                        # Borrar imágenes asociadas
                        images = msg.generated_images.all()
                        for img in images:
                            if img.image:
                                img.image.delete(save=False) # Borrar archivo
                            img.delete() # Borrar registro
                    
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

# --- VISTA PARA ELIMINAR HISTORIAL COMPLETO ---
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
                # Obtener todos los mensajes de este chat
                msgs = ChatMessage.objects.filter(user=user_obj, character_id=char_id)
                
                if del_imgs:
                    # Recolectar todas las imágenes de estos mensajes
                    for msg in msgs:
                        images = msg.generated_images.all()
                        for img in images:
                            if img.image:
                                img.image.delete(save=False)
                            img.delete()
                
                # Borrar los mensajes
                count, _ = msgs.delete()
                return count

            deleted_count = await perform_clear_chat(character_id, user, delete_images)
            return JsonResponse({'status': 'success', 'deleted_count': deleted_count})
                
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
            
    return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

# --- VISTA DEL WORKSPACE (ACTUALIZADA) ---
async def workspace_view(request):
    user = await get_user_from_request(request)
    if not user.is_authenticated:
        return redirect('account_login')
    
    company_settings = await get_company_settings()
    
    # Obtener todos los personajes para el modal de selección
    all_characters = await get_characters_with_images()
    
    # Verificar si hay un personaje seleccionado
    character_id = request.GET.get('character_id')
    # CAMBIO: Verificar si se solicitó cargar el historial
    should_load_history = request.GET.get('load_history') == 'true'
    
    selected_character = None
    chat_history = [] # Lista para el historial del chat central
    recent_chats = [] # Lista para la sidebar izquierda
    
    # Valores por defecto si no hay personaje
    default_width = 1024
    default_height = 1024
    default_seed = -1 # -1 significa aleatorio
    
    # Capacidades del workflow (para mostrar/ocultar checkboxes)
    workflow_capabilities = {
        'can_upscale': False,
        'can_facedetail': False
    }

    # --- NUEVO: Obtener lista de chats recientes CON IMÁGENES ---
    @sync_to_async
    def get_recent_chats_list():
        # 1. Obtener IDs de personajes con los que se ha hablado, ordenados por fecha
        recent_ids = list(ChatMessage.objects.filter(user=user)
                          .values_list('character_id', flat=True)
                          .order_by('-timestamp'))
        
        # 2. Eliminar duplicados manteniendo el orden
        seen = set()
        unique_ids = [x for x in recent_ids if not (x in seen or seen.add(x))]
        
        if not unique_ids:
            return []

        # 3. Obtener los objetos Character completos (con imágenes de catálogo)
        chars_qs = Character.objects.filter(id__in=unique_ids).prefetch_related('catalog_images_set')
        chars_dict = {c.id: c for c in chars_qs}
        
        # 4. Reconstruir la lista en el orden correcto
        ordered_chars = []
        for cid in unique_ids:
            if cid in chars_dict:
                ordered_chars.append(chars_dict[cid])
                
        return ordered_chars

    recent_chats = await get_recent_chats_list()

    if character_id:
        try:
            # Buscar en la lista ya cargada para evitar otra consulta
            selected_character = next((c for c in all_characters if str(c.id) == str(character_id)), None)

            # --- NUEVO: Extraer dimensiones y semilla por defecto del JSON del personaje ---
            if selected_character and selected_character.character_config:
                try:
                    config = json.loads(selected_character.character_config)
                    if 'width' in config: default_width = int(config['width'])
                    if 'height' in config: default_height = int(config['height'])
                    
                    # Lógica de semilla:
                    # Si seed_behavior es 'fixed', usamos la semilla guardada.
                    # Si es 'random', mostramos -1 (o vacío) para indicar aleatorio.
                    if config.get('seed_behavior') == 'fixed' and 'seed' in config:
                        default_seed = int(config['seed'])
                    else:
                        default_seed = -1
                        
                except (json.JSONDecodeError, ValueError):
                    pass # Si falla, se queda en defaults
            
            # --- NUEVO: Analizar Workflow para determinar capacidades ---
            if selected_character:
                @sync_to_async
                def get_workflow_json():
                    with open(selected_character.base_workflow.json_file.path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                
                try:
                    wf_json = await get_workflow_json()
                    # Si tiene configuración específica, la usamos para sobreescribir (aunque la estructura de nodos es la del base)
                    # Pero analyze_workflow_outputs necesita la estructura de nodos, así que usamos el base.
                    workflow_capabilities = analyze_workflow_outputs(wf_json)
                except Exception as e:
                    print(f"Error analyzing workflow: {e}")

            # --- CAMBIO: Cargar Historial SOLO SI SE SOLICITA ---
            if selected_character and should_load_history:
                chat_qs = await sync_to_async(list)(
                    ChatMessage.objects.filter(
                        user=user, 
                        character=selected_character
                    ).prefetch_related('generated_images').order_by('timestamp')
                )
                
                # Formatear para el template
                for msg in chat_qs:
                    item = {
                        'id': msg.id, # Necesario para borrar
                        'is_user': msg.is_from_user,
                        'text': msg.message,
                        'images': []
                    }
                    if not msg.is_from_user:
                        # Obtener imágenes asociadas
                        imgs = await sync_to_async(list)(msg.generated_images.all())
                        
                        # --- LÓGICA DE PLACEHOLDERS ---
                        # Si hay menos imágenes de las que debería haber, rellenamos con placeholders
                        real_images_count = len(imgs)
                        expected_count = msg.image_count
                        
                        # Primero agregamos las imágenes reales
                        for img in imgs:
                            img_type = "NORMAL"
                            if "UpScaler" in img.image.name: img_type = "UPSCALER"
                            elif "FaceDetailer" in img.image.name: img_type = "FACEDETAILER"
                            
                            item['images'].append({
                                'url': img.image.url,
                                'type': img_type,
                                'width': img.width, # Pasamos dimensiones
                                'height': img.height,
                                'is_deleted': False
                            })
                        
                        # Luego rellenamos con placeholders si faltan
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
        'default_width': default_width, # Pasamos al contexto
        'default_height': default_height, # Pasamos al contexto
        'default_seed': default_seed, # Pasamos la semilla al contexto
        'chat_history': chat_history, # Pasamos el historial del chat
        'recent_chats': recent_chats, # Pasamos la lista de chats recientes
        'workflow_capabilities': workflow_capabilities # Pasamos las capacidades
    }
    return await sync_to_async(render)(request, 'myapp/workspace.html', context)

async def generate_image_view(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    user = await get_user_from_request(request)

    if is_ajax:
        if request.method == 'POST':
            if not user.is_authenticated:
                return JsonResponse({'status': 'error', 'message': 'You must be logged in to generate images.'}, status=401)
            
            # --- RATE LIMITING REAL (CACHE) ---
            # Usamos el ID del usuario como clave, no la sesión.
            # Esto evita que borren cookies para saltarse el límite.
            cache_key = f"gen_limit_{user.id}"
            
            # Verificar si existe la clave en caché
            if cache.get(cache_key):
                ttl = cache.ttl(cache_key) # Tiempo restante
                return JsonResponse({'status': 'error', 'message': f'Please wait {ttl} seconds before generating another image.'}, status=429)
            
            # Establecer el bloqueo por 10 segundos
            cache.set(cache_key, True, timeout=10)
            # ----------------------------------

            character_id = request.POST.get('character_id')
            user_prompt = request.POST.get('prompt')
            
            if len(user_prompt) > 5000:
                return JsonResponse({'status': 'error', 'message': 'Prompt is too long (max 5000 characters).'}, status=400)
            
            width = request.POST.get('width')
            height = request.POST.get('height')
            seed = request.POST.get('seed')
            
            use_normal = request.POST.get('use_normal') == 'true'
            use_upscale = request.POST.get('use_upscale') == 'true'
            use_facedetailer = request.POST.get('use_facedetailer') == 'true'
            
            allowed_types = []
            if use_normal: allowed_types.append("Gen_Normal")
            if use_upscale: allowed_types.append("Gen_UpScaler")
            if use_facedetailer: allowed_types.append("Gen_FaceDetailer")
            
            if not allowed_types: allowed_types.append("Gen_Normal")

            try:
                character = await sync_to_async(Character.objects.select_related('base_workflow').get)(id=character_id)

                @sync_to_async
                def save_user_message():
                    return ChatMessage.objects.create(user=user, character=character, message=user_prompt, is_from_user=True)
                user_msg = await save_user_message()

                images_data_list, prompt_id = await generate_image_from_character(
                    character, user_prompt, width, height, seed=seed, allowed_types=allowed_types
                )

                if images_data_list:
                    generated_results = []
                    created_images = []
                    
                    @sync_to_async
                    def save_generated_image(img_bytes, classification, index):
                        new_image = CharacterImage(character=character, user=user, description=user_prompt)
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
                        img_obj = await save_generated_image(img_bytes, classification, i)
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
                # Captura errores de conexión específicos
                return JsonResponse({'status': 'error', 'message': 'Could not connect to the generation server. Please try again later.'}, status=503)
            except Exception as e:
                # Para cualquier otro error, loguea el error real en la consola del servidor
                print(f"An unexpected error occurred: {e}")
                # Y muestra un mensaje genérico al usuario
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
        characters = await get_characters_with_images()
        company_settings = await get_company_settings()
        
        # --- LÓGICA DEL CARRUSEL HERO (NUEVA) ---
        hero_items = []
        if company_settings:
            # Obtener imágenes del carrusel directamente del modelo HeroCarouselImage
            hero_images = await sync_to_async(list)(company_settings.hero_images.all())
            
            for img in hero_images:
                hero_items.append({
                    'image_url': img.image.url,
                    'name': img.caption or "" # Usar caption o vacío
                })
        
        context = {
            'characters': characters,
            'company': company_settings,
            'hero_items': hero_items
        }
        return await sync_to_async(render)(request, 'myapp/generate.html', context)
    
    return redirect('generate_image')
