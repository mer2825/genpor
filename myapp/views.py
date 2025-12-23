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
from django.db.models import Prefetch, Q
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
    return CompanySettings.objects.first()

# --- FUNCIÓN AUXILIAR PARA OBTENER EL USUARIO DE FORMA SEGURA ---
@sync_to_async
def get_user_from_request(request):
    user = request.user
    if user.is_authenticated:
        pass 
    return user

async def generate_image_view(request):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    user = await get_user_from_request(request)

    if is_ajax:
        if request.method == 'POST':
            if not user.is_authenticated:
                return JsonResponse({'status': 'error', 'message': 'Debes iniciar sesión para generar imágenes.'}, status=401)
            
            character_id = request.POST.get('character_id')
            user_prompt = request.POST.get('prompt')
            
            if len(user_prompt) > 500:
                return JsonResponse({'status': 'error', 'message': 'El prompt es demasiado largo (máximo 500 caracteres).'}, status=400)
            
            last_generation = request.session.get('last_generation_time')
            now = timezone.now().timestamp()
            
            if last_generation and (now - last_generation) < 10:
                wait_time = int(10 - (now - last_generation))
                return JsonResponse({'status': 'error', 'message': f'Por favor espera {wait_time} segundos antes de generar otra imagen.'}, status=429)
            
            request.session['last_generation_time'] = now

            try:
                character = await sync_to_async(Character.objects.select_related('base_workflow').get)(id=character_id)

                # USAMOS EL SERVICIO CENTRALIZADO
                image_bytes, prompt_id = await generate_image_from_character(character, user_prompt)

                if image_bytes:
                    @sync_to_async
                    def save_generated_image():
                        new_image = CharacterImage(character=character, user=user, description=user_prompt)
                        filename = f"user_gen_{character.name}_{prompt_id}.png"
                        new_image.image.save(filename, ContentFile(image_bytes), save=True)
                        return new_image.image.name

                    image_name = await save_generated_image()
                    image_url = reverse('serve_private_media', kwargs={'path': image_name})
                    return JsonResponse({'status': 'success', 'image_url': image_url})
                
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
        context = {
            'characters': characters,
            'company': company_settings
        }
        return await sync_to_async(render)(request, 'myapp/generate.html', context)
    
    return redirect('generate_image')
