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
    # --- CORRECCIÓN: Limpiar protocolo de la URL base ---
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
        # --- CORRECCIÓN: Limpiar protocolo aquí también ---
        return configs[0].base_url.replace("http://", "").replace("https://", "").rstrip('/')

    async with httpx.AsyncClient() as client:
        tasks = [check_video_gpu_load(client, config) for config in configs]
        results = await asyncio.gather(*tasks)
    
    results.sort(key=lambda x: x[1])
    best_address, load = results[0]
    
    if load == 9999:
        # --- CORRECCIÓN: Limpiar protocolo en fallback ---
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
        # Asegurarse de estar al inicio
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
        # ComfyUI upload endpoint: /upload/image
        response = await client.post(f"{protocol}://{address}/upload/image", files=files)
        response.raise_for_status()
        # Retorna ej: {'name': 'filename.png', 'subfolder': '', 'type': 'input'}
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
    except Exception as e:
        print(f"Error queueing prompt: {e}")
        raise

async def get_video_file(client, filename, subfolder, folder_type, address):
    protocol, _ = get_protocols(address)
    params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    try:
        response = await client.get(f"{protocol}://{address}/view", params=params)
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
        "loras": [],
        "vae": None,
        "clip": None
    }
    
    if not isinstance(workflow_json, dict):
        return analysis

    for node_id, details in workflow_json.items():
        if not isinstance(details, dict): continue
        
        class_type = details.get("class_type", "")
        inputs = details.get("inputs", {})
        title = details.get("_meta", {}).get("title", "").upper()

        # 1. UNETs (High/Low)
        if class_type == "UNETLoader":
            if "HIGH" in title:
                analysis["unet_high"] = inputs.get("unet_name")
            elif "LOW" in title:
                analysis["unet_low"] = inputs.get("unet_name")
        
        # 2. VAE
        elif class_type == "VAELoader":
            analysis["vae"] = inputs.get("vae_name")
            
        # 3. CLIP
        elif class_type == "CLIPLoader":
            analysis["clip"] = inputs.get("clip_name")

        # 4. LoRAs (DW_LoRAStackApplySimple)
        elif class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                lora_name = inputs.get(f"lora_{i}_name")
                if lora_name and lora_name != "None":
                    analysis["loras"].append({
                        "name": lora_name,
                        "strength": inputs.get(f"lora_{i}_strength", 1.0)
                    })
        
        # 5. LoRAs (LoraLoaderModelOnly - Nodos 26, 27)
        elif class_type == "LoraLoaderModelOnly":
             lora_name = inputs.get("lora_name")
             if lora_name and lora_name != "None":
                 analysis["loras"].append({
                     "name": lora_name,
                     "strength": inputs.get("strength_model", 1.0)
                 })

    return analysis

def update_video_workflow(workflow, params, uploaded_image_name):
    """
    Actualiza el workflow de video con los parámetros del usuario y la configuración del admin.
    """
    # Copia profunda
    wf = json.loads(json.dumps(workflow))
    
    # --- PARÁMETROS DE USUARIO (Runtime) ---
    
    # 1. Imagen (Node 2)
    if "2" in wf and uploaded_image_name:
        wf["2"]["inputs"]["image"] = uploaded_image_name

    # 2. Prompts
    if "17" in wf and "prompt" in params:
        wf["17"]["inputs"]["text"] = params["prompt"]
    
    if "10" in wf and "negative_prompt" in params:
        wf["10"]["inputs"]["text"] = params["negative_prompt"]

    # 3. Configuración Numérica
    if "29" in wf and "duration" in params:
        wf["29"]["inputs"]["value"] = int(params["duration"])
        
    if "30" in wf and "fps" in params:
        wf["30"]["inputs"]["value"] = int(params["fps"])
        
    if "32" in wf and "resolution" in params:
        wf["32"]["inputs"]["value"] = int(params["resolution"])

    # 4. Seed
    used_seed = params.get("seed")
    if used_seed is None or str(used_seed) == "-1" or str(used_seed) == "":
        used_seed = random.randint(0, 2147483647)
    else:
        try:
            used_seed = int(used_seed)
        except ValueError:
            used_seed = random.randint(0, 2147483647)

    if "6" in wf:
        wf["6"]["inputs"]["seed"] = used_seed

    # --- PARÁMETROS DE ADMIN (Configuración Guardada) ---
    # Si params tiene claves de configuración (unet_high, unet_low, etc.), úsalas.
    # Esto asume que 'params' puede venir mezclado o que se inyectan antes.
    
    # UNET High (Node 5)
    if "unet_high" in params and "5" in wf:
        wf["5"]["inputs"]["unet_name"] = params["unet_high"]
        
    # UNET Low (Node 4)
    if "unet_low" in params and "4" in wf:
        wf["4"]["inputs"]["unet_name"] = params["unet_low"]
        
    # VAE (Node 1)
    if "vae" in params and "1" in wf:
        wf["1"]["inputs"]["vae_name"] = params["vae"]
        
    # CLIP (Node 7)
    if "clip" in params and "7" in wf:
        wf["7"]["inputs"]["clip_name"] = params["clip"]

    # LoRAs (DW_LoRAStackApplySimple - Nodos 15, 16)
    # Nota: La lógica aquí es compleja porque hay múltiples nodos de stack.
    # Simplificación: Si hay una lista de loras en params, intentamos llenar los stacks.
    if "lora_names" in params and "lora_strengths" in params:
        lora_names = params["lora_names"]
        lora_strengths = params["lora_strengths"]
        
        # Limpiar stacks primero
        for node_id in ["15", "16"]:
            if node_id in wf:
                for i in range(1, 7):
                    wf[node_id]["inputs"][f"lora_{i}_name"] = "None"
                    wf[node_id]["inputs"][f"lora_{i}_strength"] = 1.0
        
        # Llenar stacks (hasta 12 loras en total si usamos 15 y 16)
        current_lora_idx = 0
        for node_id in ["15", "16"]:
            if node_id in wf:
                for i in range(1, 7):
                    if current_lora_idx < len(lora_names):
                        wf[node_id]["inputs"][f"lora_{i}_name"] = lora_names[current_lora_idx]
                        try:
                            wf[node_id]["inputs"][f"lora_{i}_strength"] = float(lora_strengths[current_lora_idx])
                        except:
                            wf[node_id]["inputs"][f"lora_{i}_strength"] = 1.0
                        current_lora_idx += 1
    
    # --- CORRECCIÓN CRÍTICA: Forzar guardado de video ---
    # Buscar nodos de tipo DW_Img2Vid y asegurar save_output=True
    for node_id, details in wf.items():
        if details.get("class_type") == "DW_Img2Vid":
            if "inputs" in details:
                print(f"DEBUG: Forcing save_output=True for node {node_id} (DW_Img2Vid)")
                details["inputs"]["save_output"] = True

    return wf, used_seed

# --- GENERACIÓN PRINCIPAL ---

async def generate_video_task(user_image_file, prompt, negative_prompt, duration, fps, resolution, seed=None):
    """
    Orquesta la generación de video.
    Retorna: (video_content_bytes, used_seed, video_filename)
    """
    # 1. Obtener dirección GPU
    address = await get_active_video_comfyui_address()
    client_id = str(uuid.uuid4())
    _, ws_protocol = get_protocols(address)
    
    # 2. Cargar Workflow Base
    @sync_to_async
    def get_active_workflow():
        # Obtiene el primer workflow de video disponible
        return VideoWorkflow.objects.first()
    
    video_wf_obj = await get_active_workflow()
    if not video_wf_obj:
        raise Exception("No hay VideoWorkflow configurado en el sistema.")
        
    @sync_to_async
    def read_json(path):
        # --- CORRECCIÓN: Añadido encoding='utf-8' ---
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        
    workflow_json = await read_json(video_wf_obj.json_file.path)
    
    # --- NUEVO: Cargar configuración activa del admin ---
    admin_config = {}
    if video_wf_obj.active_config:
        try:
            admin_config = json.loads(video_wf_obj.active_config)
        except json.JSONDecodeError:
            pass

    # 3. Conexión
    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "MyApp/Video/1.0"}
    
    async with httpx.AsyncClient(timeout=600.0, headers=headers) as client:
        # A. Subir Imagen
        upload_resp = await upload_image_to_comfyui(client, user_image_file, address)
        uploaded_filename = upload_resp.get("name")
        
        # B. Preparar Params (Mezclando usuario + admin)
        params = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "fps": fps,
            "resolution": resolution,
            "seed": seed,
            **admin_config # Inyectar configuración del admin (unets, loras, etc.)
        }
        
        # C. Actualizar Workflow
        final_workflow, used_seed = update_video_workflow(workflow_json, params, uploaded_filename)
        
        # D. WebSocket y Ejecución
        uri = f"{ws_protocol}://{address}/ws?clientId={client_id}"
        async with websockets.connect(uri) as websocket:
            queued = await queue_prompt(client, final_workflow, client_id, address)
            prompt_id = queued['prompt_id']
            
            # Esperar finalización
            while True:
                try:
                    out = await websocket.recv()
                    if isinstance(out, str):
                        msg = json.loads(out)
                        if msg['type'] == 'execution_error':
                            raise Exception(f"ComfyUI Error: {msg['data']}")
                        if msg['type'] == 'executing' and msg['data']['node'] is None and msg['data']['prompt_id'] == prompt_id:
                            break
                except websockets.exceptions.ConnectionClosed:
                    break
            
            # E. Obtener Resultado
            history = await get_history(client, prompt_id, address)
            outputs = history[prompt_id]['outputs']
            
            # DEBUG: Imprimir outputs para diagnóstico
            print(f"DEBUG: ComfyUI Outputs for {prompt_id}: {json.dumps(outputs, indent=2)}")
            
            video_content = None
            video_filename = f"video_{prompt_id}.mp4"
            
            # Buscar salida de video
            for node_id, output_data in outputs.items():
                # Caso 1: VideoHelperSuite (gifs)
                if 'gifs' in output_data: 
                     for vid in output_data['gifs']:
                         video_content = await get_video_file(client, vid['filename'], vid['subfolder'], vid['type'], address)
                         video_filename = vid['filename']
                         if video_content: break
                
                # Caso 2: Standard Save (images) pero con extensión de video
                if not video_content and 'images' in output_data:
                     for img in output_data['images']:
                         fname = img['filename']
                         if fname.endswith('.mp4') or fname.endswith('.gif') or fname.endswith('.webm'):
                             video_content = await get_video_file(client, fname, img['subfolder'], img['type'], address)
                             video_filename = fname
                             if video_content: break
                
                # --- NUEVO: Caso 3: Salida explícita "video" (DW_Img2Vid) ---
                if not video_content and 'video' in output_data:
                     for vid in output_data['video']:
                         video_content = await get_video_file(client, vid['filename'], vid['subfolder'], vid['type'], address)
                         video_filename = vid['filename']
                         if video_content: break

                if video_content: break
            
            if not video_content:
                raise Exception("No se encontró el archivo de video generado en la respuesta de ComfyUI.")

            return video_content, used_seed, video_filename