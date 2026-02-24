import json
import uuid
import random
import httpx
import websockets
import asyncio
import os
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from .models import VideoConnectionConfig, VideoWorkflow


# --- CONFIGURACIÓN Y RED (VIDEO) ---

def get_protocols(address):
    """Determina si usar HTTP/WS o HTTPS/WSS basado en la dirección."""
    if "runpod.net" in address or "cloudflare" in address or "ngrok" in address:
        return "https", "wss"
    return "http", "ws"


def get_active_video_configs_sync():
    """Helper síncrono para obtener configs de video de la DB."""
    return list(VideoConnectionConfig.objects.filter(is_active=True))


async def check_video_gpu_load(client, config):
    """
    Consulta la API de ComfyUI para ver la carga de la GPU de video.
    """
    address = config.base_url.replace("http://", "").replace("https://", "").rstrip('/')
    protocol, _ = get_protocols(address)
    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "MyApp/Video/1.0"}

    try:
        response = await client.get(f"{protocol}://{address}/queue", headers=headers, timeout=2.0)
        if response.status_code == 200:
            data = response.json()
            running = len(data.get('queue_running', []))
            pending = len(data.get('queue_pending', []))
            return (address, running + pending)
    except Exception:
        pass
    return (address, 9999)


async def get_active_video_comfyui_address():
    """
    Obtiene la dirección de ComfyUI para video más libre.
    """
    configs = await sync_to_async(get_active_video_configs_sync)()
    if not configs:
        return "127.0.0.1:8188"

    if len(configs) == 1:
        return configs[0].base_url.replace("http://", "").replace("https://", "").rstrip('/')

    async with httpx.AsyncClient() as client:
        tasks = [check_video_gpu_load(client, config) for config in configs]
        results = await asyncio.gather(*tasks)

    results.sort(key=lambda x: x[1])
    best_address, load = results[0]

    if load == 9999:
        return configs[0].base_url.replace("http://", "").replace("https://", "").rstrip('/')

    return best_address


# --- API COMFYUI (VIDEO) ---

async def upload_image_to_comfyui(client, image_file, address):
    """
    Sube la imagen fuente a ComfyUI.
    image_file: Puede ser un objeto File de Django o un path string.
    """
    protocol, _ = get_protocols(address)
    files = {}

    # Manejo si es un objeto File de Django (tiene .name y .read)
    if hasattr(image_file, 'read'):
        if hasattr(image_file, 'seek'):
            image_file.seek(0)
        file_content = image_file.read()
        filename = os.path.basename(image_file.name)
        files = {'image': (filename, file_content, 'image/png')}
    # Manejo si es un path string
    elif isinstance(image_file, str) and os.path.exists(image_file):
        filename = os.path.basename(image_file)
        with open(image_file, 'rb') as f:
            file_content = f.read()
        files = {'image': (filename, file_content, 'image/png')}
    else:
        raise ValueError("Archivo de imagen inválido para subir.")

    try:
        response = await client.post(f"{protocol}://{address}/upload/image", files=files)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error subiendo imagen a ComfyUI: {e}")
        raise


async def queue_prompt(client, prompt_workflow, client_id, address):
    protocol, _ = get_protocols(address)
    p = {"prompt": prompt_workflow, "client_id": client_id}
    try:
        response = await client.post(f"{protocol}://{address}/prompt", json=p)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        # --- AQUÍ CAPTURAMOS EL ERROR EXACTO DE COMFYUI ---
        error_details = e.response.text
        print(f"🛑 ComfyUI Error 400 - Detalles de Validación: {error_details}")
        raise Exception(f"ComfyUI Error: Validation Failed - {error_details}")
    except Exception as e:
        print(f"Error queueing prompt: {e}")
        raise


async def get_video_file(client, filename, subfolder, folder_type, address):
    protocol, _ = get_protocols(address)
    params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    try:
        # Aumentamos timeout para la descarga del video final
        response = await client.get(f"{protocol}://{address}/view", params=params, timeout=120.0)
        response.raise_for_status()
        return response.content
    except Exception as e:
        print(f"Error descargando video: {e}")
        return None


async def get_history(client, prompt_id, address):
    protocol, _ = get_protocols(address)
    response = await client.get(f"{protocol}://{address}/history/{prompt_id}")
    response.raise_for_status()
    return response.json()


# --- LOGICA WORKFLOW VIDEO ---

def analyze_video_workflow(workflow_json):
    """
    Analiza el workflow de video para extraer parámetros configurables.
    """
    analysis = {
        "unet_high": None,
        "unet_low": None,
        "loras_high": [],
        "loras_low": [],
        "vae": None,
        "clip": None,
        "black_list_tags": None, # NUEVO
        "enable_blacklist": True # Default
    }

    if not isinstance(workflow_json, dict):
        return analysis

    for node_id, details in workflow_json.items():
        if not isinstance(details, dict): continue

        class_type = details.get("class_type", "")
        inputs = details.get("inputs", {})
        title = details.get("_meta", {}).get("title", "").upper()

        if class_type == "UNETLoader":
            if "HIGH" in title:
                analysis["unet_high"] = inputs.get("unet_name")
            elif "LOW" in title:
                analysis["unet_low"] = inputs.get("unet_name")
        elif class_type == "VAELoader":
            analysis["vae"] = inputs.get("vae_name")
        elif class_type == "CLIPLoader":
            analysis["clip"] = inputs.get("clip_name")
        elif class_type == "DW_LoRAStackApplySimple":
            target_list = None
            if "HIGH" in title:
                target_list = analysis["loras_high"]
            elif "LOW" in title:
                target_list = analysis["loras_low"]

            if target_list is not None:
                for i in range(1, 7):
                    lora_name = inputs.get(f"lora_{i}_name")
                    if lora_name and lora_name != "None":
                        target_list.append({
                            "name": lora_name,
                            "strength": inputs.get(f"lora_{i}_strength", 1.0)
                        })
        elif class_type == "LoraLoaderModelOnly":
            lora_name = inputs.get("lora_name", "")
            target_list = None

            if "high" in lora_name.lower() or node_id == "26":
                target_list = analysis["loras_high"]
            elif "low" in lora_name.lower() or node_id == "27":
                target_list = analysis["loras_low"]

            if target_list is not None and lora_name:
                target_list.append({
                    "name": lora_name,
                    "strength": inputs.get("strength_model", 1.0),
                    "is_legacy_node": True,
                    "node_id": node_id
                })
        
        # --- NUEVO: Detectar BLACK_LIST_TAGS ---
        elif title == "BLACK_LIST_TAGS" and class_type == "DW_Text":
            analysis["black_list_tags"] = inputs.get("text", "")

    return analysis


def update_video_workflow(workflow, params, uploaded_image_name):
    """
    Actualiza el workflow de video con los parámetros de la petición web.
    Se limita exclusivamente a la imagen, los prompts, resolución, seed, duración y FPS.
    """
    # Copia profunda
    wf = json.loads(json.dumps(workflow))

    # --- 1. Imagen (Node 2) ---
    if "2" in wf and uploaded_image_name:
        wf["2"]["inputs"]["image"] = uploaded_image_name

    # --- 2. Prompts (Nodos 14 y 17) ---
    # NOTA: En el JSON analizado, el prompt de usuario es el nodo 28 (PROMP_USUARIO)
    if "28" in wf and "prompt" in params:
        wf["28"]["inputs"]["text"] = params["prompt"]

    # NOTA: En el JSON analizado, el negative prompt es el nodo 30 (Negative Promp)
    if "30" in wf and "negative_prompt" in params:
        wf["30"]["inputs"]["text"] = params["negative_prompt"]

    # --- 3. 'RES_LADO' > nodo 26 (Resolución) ---
    # NOTA: En el JSON analizado, RES_LADO es el nodo 26
    if "26" in wf:
        res = int(params.get("resolution", 768))  # Default 768 si no se especifica
        wf["26"]["inputs"]["value"] = res

    # --- 4. 'DW_seed' > nodo 12 ---
    # NOTA: En el JSON analizado, DW_seed es el nodo 12
    used_seed = params.get("seed")
    if used_seed is None or str(used_seed) == "-1" or str(used_seed) == "":
        used_seed = random.randint(0, 2147483647)
    else:
        try:
            used_seed = int(used_seed)
        except ValueError:
            used_seed = random.randint(0, 2147483647)

    if "12" in wf:
        wf["12"]["inputs"]["seed"] = used_seed

    # --- 5. 'SEGUNDOS' > nodo 18 ---
    # NOTA: En el JSON analizado, SEGUNDOS es el nodo 18
    if "18" in wf:
        duration_val = int(params.get("duration", 3))
        wf["18"]["inputs"]["value"] = duration_val

    # --- 6. 'FPS' > nodo 3 ---
    # NOTA: En el JSON analizado, FPS es el nodo 3
    if "3" in wf:
        fps_val = int(params.get("fps", 24))
        wf["3"]["inputs"]["value"] = fps_val

    # --- 7. BLACK_LIST_TAGS > nodo 23 ---
    # Si viene en params (desde la configuración activa), lo inyectamos
    if "23" in wf:
        enable_blacklist = params.get("enable_blacklist", True)
        
        if enable_blacklist:
            # Si está activado, usamos el texto configurado (o el default si no hay)
            if "black_list_tags" in params:
                wf["23"]["inputs"]["text"] = params["black_list_tags"]
        else:
            # Si está desactivado, enviamos cadena vacía
            wf["23"]["inputs"]["text"] = ""

    # Forzar guardado de video (Node 17 - DW_Img2Vid)
    # NOTA: En el JSON analizado, DW_Img2Vid es el nodo 17
    if "17" in wf:
        wf["17"]["inputs"]["save_output"] = True

    return wf, used_seed


# --- GENERACIÓN PRINCIPAL ---

async def generate_video_task(user_image_file, prompt, negative_prompt, duration, fps, quality, seed=None,
                              resolution=768):
    """
    Orquesta la generación de video.
    Retorna: (video_content_bytes, used_seed, video_filename, final_workflow)
    """
    print(f"🚀 INICIANDO GENERACIÓN DE VIDEO: {prompt[:30]}...")
    
    # 1. Obtener dirección GPU
    address = await get_active_video_comfyui_address()
    print(f"📡 Conectando a ComfyUI en: {address}")
    
    client_id = str(uuid.uuid4())
    _, ws_protocol = get_protocols(address)

    # 2. Cargar Workflow Base
    @sync_to_async
    def get_active_workflow():
        return VideoWorkflow.objects.first()

    video_wf_obj = await get_active_workflow()
    if not video_wf_obj:
        raise Exception("No hay VideoWorkflow configurado en el sistema.")

    @sync_to_async
    def read_json(path):
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)

    workflow_json = await read_json(video_wf_obj.json_file.path)
    
    # --- NUEVO: Cargar configuración activa (si existe) ---
    active_config = {}
    if video_wf_obj.active_config:
        try:
            active_config = json.loads(video_wf_obj.active_config)
        except json.JSONDecodeError:
            pass

    # 3. Conexión
    # Aumentamos el timeout global del cliente HTTP a 1200 segundos (20 minutos)
    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "MyApp/Video/1.0"}

    async with httpx.AsyncClient(timeout=1200.0, headers=headers) as client:
        # A. Subir Imagen
        print("📤 Subiendo imagen...")
        upload_resp = await upload_image_to_comfyui(client, user_image_file, address)
        uploaded_filename = upload_resp.get("name")

        # B. Preparar Params (Solo los necesarios)
        params = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "fps": fps,
            "resolution": resolution,
            "seed": seed,
            # Inyectar Blacklist desde la config activa
            "black_list_tags": active_config.get("black_list_tags"),
            "enable_blacklist": active_config.get("enable_blacklist", True) # Default True
        }

        # C. Actualizar Workflow
        final_workflow, used_seed = update_video_workflow(workflow_json, params, uploaded_filename)

        # D. WebSocket y Ejecución
        uri = f"{ws_protocol}://{address}/ws?clientId={client_id}"
        
        try:
            print("🔌 Conectando WebSocket...")
            # ping_interval=None evita que se cierre la conexión si el servidor está ocupado
            async with websockets.connect(uri, ping_interval=None) as websocket:
                print("📨 Enviando Prompt a la cola...")
                queued = await queue_prompt(client, final_workflow, client_id, address)
                prompt_id = queued['prompt_id']
                print(f"✅ Prompt en cola. ID: {prompt_id}. Esperando ejecución...")

                # Esperar finalización
                while True:
                    try:
                        out = await websocket.recv()
                        if isinstance(out, str):
                            msg = json.loads(out)
                            if msg['type'] == 'execution_error':
                                print(f"❌ Error de ejecución ComfyUI: {msg['data']}")
                                raise Exception(f"ComfyUI Error: {msg['data']}")
                            if msg['type'] == 'executing':
                                node = msg['data']['node']
                                if node is None and msg['data']['prompt_id'] == prompt_id:
                                    print("🏁 Ejecución finalizada.")
                                    break
                                else:
                                    # Opcional: Imprimir progreso de nodos
                                    # print(f"🔄 Ejecutando nodo: {node}")
                                    pass
                    except websockets.exceptions.ConnectionClosed:
                        print("⚠️ WebSocket cerrado inesperadamente.")
                        break

            # E. Obtener Resultado
            print("📥 Obteniendo historial y descargando video...")
            history = await get_history(client, prompt_id, address)
            outputs = history[prompt_id]['outputs']

            # print(f"DEBUG: ComfyUI Outputs for {prompt_id}: {json.dumps(outputs, indent=2)}")

            video_content = None
            video_filename = f"video_{prompt_id}.mp4"

            # Buscar salida de video
            for node_id, output_data in outputs.items():
                if 'gifs' in output_data:
                    for vid in output_data['gifs']:
                        video_content = await get_video_file(client, vid['filename'], vid['subfolder'], vid['type'],
                                                             address)
                        video_filename = vid['filename']
                        if video_content: break

                if not video_content and 'images' in output_data:
                    for img in output_data['images']:
                        fname = img['filename']
                        if fname.endswith('.mp4') or fname.endswith('.gif') or fname.endswith('.webm'):
                            video_content = await get_video_file(client, fname, img['subfolder'], img['type'], address)
                            video_filename = fname
                            if video_content: break

                if not video_content and 'video' in output_data:
                    for vid in output_data['video']:
                        video_content = await get_video_file(client, vid['filename'], vid['subfolder'], vid['type'],
                                                             address)
                        video_filename = vid['filename']
                        if video_content: break

                if video_content: break

            if not video_content:
                raise Exception("No se encontró el archivo de video generado en la respuesta de ComfyUI.")
            
            print("✨ Video descargado correctamente.")
            return video_content, used_seed, video_filename, final_workflow

        except Exception as e:
            print(f"❌ Error CRÍTICO en generate_video_task: {e}")
            raise