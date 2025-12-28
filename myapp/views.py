from django.shortcuts import render, redirect, get_object_or_404
from django.db import models
from .models import Workflow, Character, CharacterImage, ConnectionConfig, CompanySettings
import json
import os
from django.conf import settings
from asgiref.sync import sync_to_async
from django.http import JsonResponse, FileResponse, Http404
from django.core.files.base import ContentFile
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.urls import reverse
from django.db.models import Prefetch, Q, Count
from django.contrib.auth.models import User
from .services import generate_image_from_character, get_active_comfyui_address, get_comfyui_object_info, analyze_workflow, update_workflow, queue_prompt, get_history, get_image, get_protocols

# --- VISTA SEGURA PARA SERVIR ARCHIVOS ---
def serve_private_media(request, path):
    """
    Sirve archivos de la carpeta 'user_images'.
    Permite acceso si:
    1. El usuario solicitante es el dueño.
    2. El usuario solicitante es staff.
    3. El dueño de la imagen es staff (imagen pública/oficial).
    """
    file_path = os.path.join(settings.MEDIA_ROOT, path)
    
    if not os.path.abspath(file_path).startswith(os.path.abspath(settings.MEDIA_ROOT)):
        raise Http404("Ruta de archivo no válida.")

    try:
        parts = path.split('/')
        if parts[0] == 'user_images' and len(parts) > 1:
            owner_id = int(parts[1])
        else:
            raise Http404("No es un archivo de usuario.")
    except (ValueError, IndexError):
        raise Http404("Ruta de archivo mal formada.")

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
            raise Http404("El archivo no existe.")
    else:
        raise Http404("Acceso denegado.")

# --- NUEVA FUNCIÓN SEGURA PARA OBTENER PERSONAJES ---
@sync_to_async
def get_characters_with_images():
    # Incluir imágenes sin usuario (legacy) O imágenes de usuarios staff
    public_images_prefetch = Prefetch(
        'images',
        queryset=CharacterImage.objects.filter(Q(user__isnull=True) | Q(user__is_staff=True)),
        to_attr='public_images'
    )
    return list(Character.objects.prefetch_related(public_images_prefetch).all())

# --- FUNCIÓN PARA OBTENER LA CONFIGURACIÓN DE LA EMPRESA ---
@sync_to_async
def get_company_settings():
    # Prefetch para obtener los personajes del carrusel y sus imágenes públicas
    public_images_prefetch = Prefetch(
        'images',
        queryset=CharacterImage.objects.filter(Q(user__isnull=True) | Q(user__is_staff=True)),
        to_attr='public_images'
    )
    return CompanySettings.objects.prefetch_related(
        Prefetch('hero_characters', queryset=Character.objects.prefetch_related(public_images_prefetch))
    ).first()

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
        return JsonResponse({'status': 'error', 'message': 'No autorizado'}, status=401)
    
    if request.method == 'POST':
        try:
            image_ids = request.POST.getlist('image_ids[]')
            if not image_ids:
                return JsonResponse({'status': 'error', 'message': 'No se seleccionaron imágenes'})
            
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
    
    return JsonResponse({'status': 'error', 'message': 'Método no permitido'}, status=405)

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
    selected_character = None
    
    # Valores por defecto si no hay personaje
    default_width = 1024
    default_height = 1024
    
    if character_id:
        try:
            # Buscar en la lista ya cargada para evitar otra consulta
            selected_character = next((c for c in all_characters if str(c.id) == str(character_id)), None)
            
            # --- NUEVO: Extraer dimensiones por defecto del JSON del personaje ---
            if selected_character and selected_character.character_config:
                try:
                    config = json.loads(selected_character.character_config)
                    if 'width' in config: default_width = int(config['width'])
                    if 'height' in config: default_height = int(config['height'])
                except (json.JSONDecodeError, ValueError):
                    pass # Si falla, se queda en 1024
                    
        except Exception:
            pass

    context = {
        'company': company_settings,
        'selected_character': selected_character,
        'all_characters': all_characters,
        'default_width': default_width, # Pasamos al contexto
        'default_height': default_height # Pasamos al contexto
    }
    return await sync_to_async(render)(request, 'myapp/workspace.html', context)

async def generate_image_view(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    user = await get_user_from_request(request)

    if is_ajax:
        if request.method == 'POST':
            if not user.is_authenticated:
                return JsonResponse({'status': 'error', 'message': 'Debes iniciar sesión para generar imágenes.'}, status=401)
            
            character_id = request.POST.get('character_id')
            user_prompt = request.POST.get('prompt')
            
            # CAMBIO AQUÍ: Aumentado de 500 a 5000 caracteres
            if len(user_prompt) > 5000:
                return JsonResponse({'status': 'error', 'message': 'El prompt es demasiado largo (máximo 5000 caracteres).'}, status=400)
            
            # Obtener dimensiones del cliente (si las envía)
            width = request.POST.get('width')
            height = request.POST.get('height')
            
            last_generation = request.session.get('last_generation_time')
            now = timezone.now().timestamp()
            
            if last_generation and (now - last_generation) < 10:
                wait_time = int(10 - (now - last_generation))
                return JsonResponse({'status': 'error', 'message': f'Por favor espera {wait_time} segundos antes de generar otra imagen.'}, status=429)
            
            request.session['last_generation_time'] = now

            try:
                character = await sync_to_async(Character.objects.select_related('base_workflow').get)(id=character_id)

                # USAMOS EL SERVICIO CENTRALIZADO
                # Ahora devuelve una lista de imágenes
                images_bytes_list, prompt_id = await generate_image_from_character(character, user_prompt, width, height)

                if images_bytes_list:
                    image_urls = []
                    
                    @sync_to_async
                    def save_generated_image(img_bytes, index):
                        new_image = CharacterImage(character=character, user=user, description=user_prompt)
                        # Agregamos un índice al nombre del archivo para diferenciarlas
                        filename = f"user_gen_{character.name}_{prompt_id}_{index}.png"
                        new_image.image.save(filename, ContentFile(img_bytes), save=True)
                        return new_image.image.name

                    for i, img_bytes in enumerate(images_bytes_list):
                        image_name = await save_generated_image(img_bytes, i)
                        image_url = reverse('serve_private_media', kwargs={'path': image_name})
                        image_urls.append(image_url)
                    
                    return JsonResponse({'status': 'success', 'image_urls': image_urls})
                
                return JsonResponse({'status': 'error', 'message': 'No se generó ninguna imagen.'}, status=500)

            except Character.DoesNotExist:
                return JsonResponse({'status': 'error', 'message': 'Personaje no encontrado.'}, status=404)
            except Exception as e:
                return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

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
        
        # --- LÓGICA DEL CARRUSEL HERO ---
        hero_items = []
        if company_settings:
            if company_settings.hero_mode == 'manual':
                # Obtener personajes seleccionados manualmente
                hero_chars = await sync_to_async(list)(company_settings.hero_characters.all())
                for char in hero_chars:
                    # Buscar la primera imagen pública disponible
                    if hasattr(char, 'public_images') and char.public_images:
                        hero_items.append({
                            'image_url': char.public_images[0].image.url,
                            'name': char.name
                        })
            else:
                # Modo Aleatorio: Tomamos los primeros 6 personajes que tengan imágenes
                for char in characters[:6]:
                    if hasattr(char, 'public_images') and char.public_images:
                        hero_items.append({
                            'image_url': char.public_images[0].image.url,
                            'name': char.name
                        })
        
        context = {
            'characters': characters,
            'company': company_settings,
            'hero_items': hero_items
        }
        return await sync_to_async(render)(request, 'myapp/generate.html', context)
    
    return redirect('generate_image')
