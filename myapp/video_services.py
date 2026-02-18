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
    except httpx.HTTPStatusError as e:
        # --- CAMBIO: Log simplificado ---
        print("ComfyUI Error: 400 Bad Request (Validation Failed)")
        raise Exception("ComfyUI Error: Validation Failed")
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
        "loras_high": [], # CAMBIO: Lista separada para HIGH
        "loras_low": [],  # CAMBIO: Lista separada para LOW
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
        
        # 5. LoRAs (LoraLoaderModelOnly - Nodos 26, 27) - LEGACY
        elif class_type == "LoraLoaderModelOnly":
             lora_name = inputs.get("lora_name", "")
             target_list = None
             
             # Intentar adivinar si es HIGH o LOW
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
    # Fallback para LoadImage (Node 36 en el ejemplo del usuario)
    if "36" in wf and uploaded_image_name:
        wf["36"]["inputs"]["image"] = uploaded_image_name

    # 2. Prompts
    if "17" in wf and "prompt" in params:
        wf["17"]["inputs"]["text"] = params["prompt"]
    # Fallback para CLIPTextEncode (Node 54 en el ejemplo)
    if "54" in wf and "prompt" in params:
        wf["54"]["inputs"]["text"] = params["prompt"]
    
    if "10" in wf and "negative_prompt" in params:
        wf["10"]["inputs"]["text"] = params["negative_prompt"]
    # Fallback para CLIPTextEncode Negative (Node 55 en el ejemplo)
    if "55" in wf and "negative_prompt" in params:
        wf["55"]["inputs"]["text"] = params["negative_prompt"]

    # 3. Configuración Numérica
    # Duration (Node 29 o 35)
    if "29" in wf and "duration" in params:
        wf["29"]["inputs"]["value"] = int(params["duration"])
        wf["29"]["inputs"]["int_value"] = int(params["duration"]) # Fallback
    if "35" in wf and "duration" in params:
        wf["35"]["inputs"]["value"] = int(params["duration"])
        wf["35"]["inputs"]["int_value"] = int(params["duration"]) # Fallback
        
    # FPS (Node 30 o 37)
    if "30" in wf and "fps" in params:
        wf["30"]["inputs"]["value"] = int(params["fps"])
        wf["30"]["inputs"]["int_value"] = int(params["fps"]) # Fallback
    if "37" in wf and "fps" in params:
        wf["37"]["inputs"]["value"] = int(params["fps"])
        wf["37"]["inputs"]["int_value"] = int(params["fps"]) # Fallback
        
    # Quality (Node 32 o 45) - ANTES RESOLUTION
    if "32" in wf and "quality" in params:
        wf["32"]["inputs"]["value"] = int(params["quality"])
        wf["32"]["inputs"]["int_value"] = int(params["quality"]) # Fallback
    if "45" in wf and "quality" in params:
        wf["45"]["inputs"]["value"] = int(params["quality"])
        wf["45"]["inputs"]["int_value"] = int(params["quality"]) # Fallback

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
    # Fallback para DW_seed (Node 50)
    if "50" in wf:
        wf["50"]["inputs"]["seed"] = used_seed

    # --- PARÁMETROS DE ADMIN (Configuración Guardada) ---
    
    # UNET High (Node 5 o 52)
    if "unet_high" in params:
        if "5" in wf: wf["5"]["inputs"]["unet_name"] = params["unet_high"]
        if "52" in wf: wf["52"]["inputs"]["unet_name"] = params["unet_high"]
        
    # UNET Low (Node 4 o 53)
    if "unet_low" in params:
        if "4" in wf: wf["4"]["inputs"]["unet_name"] = params["unet_low"]
        if "53" in wf: wf["53"]["inputs"]["unet_name"] = params["unet_low"]
        
    # VAE (Node 1 o 48)
    if "vae" in params:
        if "1" in wf: wf["1"]["inputs"]["vae_name"] = params["vae"]
        if "48" in wf: wf["48"]["inputs"]["vae_name"] = params["vae"]
        
    # CLIP (Node 7 o 57)
    if "clip" in params:
        if "7" in wf: wf["7"]["inputs"]["clip_name"] = params["clip"]
        if "57" in wf: wf["57"]["inputs"]["clip_name"] = params["clip"]

    # --- LoRAs HIGH ---
    if "lora_names_high" in params and "lora_strengths_high" in params:
        lora_names = params["lora_names_high"]
        lora_strengths = params["lora_strengths_high"]
        
        # 1. Buscar nodo STACK HIGH (Node 15 o 51)
        high_node_id = None
        if "15" in wf: high_node_id = "15"
        if "51" in wf: high_node_id = "51"
        
        if high_node_id:
            # Limpiar
            for i in range(1, 7):
                wf[high_node_id]["inputs"][f"lora_{i}_name"] = "None"
                wf[high_node_id]["inputs"][f"lora_{i}_strength"] = 1.0
            
            # Llenar
            for i in range(len(lora_names)):
                if i < 6:
                    wf[high_node_id]["inputs"][f"lora_{i+1}_name"] = lora_names[i]
                    try:
                        wf[high_node_id]["inputs"][f"lora_{i+1}_strength"] = float(lora_strengths[i])
                    except:
                        wf[high_node_id]["inputs"][f"lora_{i+1}_strength"] = 1.0
        
        # 2. Buscar nodo LEGACY HIGH (Node 26)
        if "26" in wf:
            # Si hay un LoRA configurado en la posición 1, usarlo. Si no, intentar poner None si es posible.
            # Nota: LoraLoaderModelOnly NO suele aceptar "None". Si no hay LoRA, esto fallará si no se borra el nodo.
            if len(lora_names) > 0 and lora_names[0] != "None":
                wf["26"]["inputs"]["lora_name"] = lora_names[0]
                try:
                    wf["26"]["inputs"]["strength_model"] = float(lora_strengths[0])
                except:
                    wf["26"]["inputs"]["strength_model"] = 1.0
            else:
                # Intento desesperado: Poner un nombre vacío o None y rezar, o borrar el nodo si pudiéramos.
                # Como no podemos borrar fácilmente, dejamos el valor original o ponemos uno dummy si existe.
                pass

    # --- LoRAs LOW ---
    if "lora_names_low" in params and "lora_strengths_low" in params:
        lora_names = params["lora_names_low"]
        lora_strengths = params["lora_strengths_low"]
        
        # 1. Buscar nodo STACK LOW (Node 16 o 56)
        low_node_id = None
        if "16" in wf: low_node_id = "16"
        if "56" in wf: low_node_id = "56"
        
        if low_node_id:
            # Limpiar
            for i in range(1, 7):
                wf[low_node_id]["inputs"][f"lora_{i}_name"] = "None"
                wf[low_node_id]["inputs"][f"lora_{i}_strength"] = 1.0
            
            # Llenar
            for i in range(len(lora_names)):
                if i < 6:
                    wf[low_node_id]["inputs"][f"lora_{i+1}_name"] = lora_names[i]
                    try:
                        wf[low_node_id]["inputs"][f"lora_{i+1}_strength"] = float(lora_strengths[i])
                    except:
                        wf[low_node_id]["inputs"][f"lora_{i+1}_strength"] = 1.0

        # 2. Buscar nodo LEGACY LOW (Node 27)
        if "27" in wf:
            if len(lora_names) > 0 and lora_names[0] != "None":
                wf["27"]["inputs"]["lora_name"] = lora_names[0]
                try:
                    wf["27"]["inputs"]["strength_model"] = float(lora_strengths[0])
                except:
                    wf["27"]["inputs"]["strength_model"] = 1.0
            else:
                pass
    
    # --- CORRECCIÓN CRÍTICA: Forzar guardado de video ---
    # Buscar nodos de tipo DW_Img2Vid y asegurar save_output=True
    for node_id, details in wf.items():
        if details.get("class_type") == "DW_Img2Vid":
            if "inputs" in details:
                # print(f"DEBUG: Forcing save_output=True for node {node_id} (DW_Img2Vid)")
                details["inputs"]["save_output"] = True

    return wf, used_seed

# --- GENERACIÓN PRINCIPAL ---

async def generate_video_task(user_image_file, prompt, negative_prompt, duration, fps, quality, seed=None):
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
            "quality": quality,
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