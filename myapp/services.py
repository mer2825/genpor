import json
import uuid
import random
import httpx
import websockets
from asgiref.sync import sync_to_async
from .models import ConnectionConfig

# --- FUNCIONES DE CONFIGURACIÓN Y RED ---

@sync_to_async
def get_active_comfyui_address():
    """Obtiene la URL base de la conexión activa desde la base de datos."""
    try:
        active_config = ConnectionConfig.objects.get(is_active=True)
        return active_config.base_url
    except ConnectionConfig.DoesNotExist:
        return "127.0.0.1:8188"

def get_protocols(address):
    """Determina si usar HTTP/WS o HTTPS/WSS basado en la dirección."""
    if "runpod.net" in address or "cloudflare" in address or "ngrok" in address:
        return "https", "wss"
    return "http", "ws"

# --- FUNCIONES DE API COMFYUI ---

async def get_comfyui_object_info(address):
    protocol, _ = get_protocols(address)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{protocol}://{address}/object_info")
            response.raise_for_status()
            data = response.json()
            return {
                "checkpoints": data.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [[]])[0],
                "vaes": data.get("VAELoader", {}).get("input", {}).get("required", {}).get("vae_name", [[]])[0],
                "loras": data.get("LoraLoader", {}).get("input", {}).get("required", {}).get("lora_name", [[]])[0],
                "samplers": data.get("KSampler", {}).get("input", {}).get("required", {}).get("sampler_name", [[]])[0],
                "schedulers": data.get("KSampler", {}).get("input", {}).get("required", {}).get("scheduler", [[]])[0],
            }
    except (httpx.RequestError, json.JSONDecodeError):
        return {"checkpoints": [], "vaes": [], "loras": [], "samplers": [], "schedulers": []}

async def queue_prompt(client, prompt_workflow, client_id, address):
    protocol, _ = get_protocols(address)
    p = {"prompt": prompt_workflow, "client_id": client_id}
    
    try:
        response = await client.post(f"{protocol}://{address}/prompt", json=p)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        # Capturar y mostrar el error detallado de ComfyUI
        error_msg = f"ComfyUI Error {e.response.status_code}: {e.response.text}"
        print(error_msg) # Imprimir en consola del servidor
        raise Exception(error_msg) # Re-lanzar para que llegue a la vista

async def get_image(client, filename, subfolder, folder_type, address):
    protocol, _ = get_protocols(address)
    response = await client.get(f"{protocol}://{address}/view?filename={filename}&subfolder={subfolder}&type={folder_type}")
    response.raise_for_status()
    return response.content

async def get_history(client, prompt_id, address):
    protocol, _ = get_protocols(address)
    response = await client.get(f"{protocol}://{address}/history/{prompt_id}")
    response.raise_for_status()
    return response.json()

# --- LÓGICA DE WORKFLOW ---

def analyze_workflow_outputs(workflow_json):
    """
    Analiza un workflow para detectar qué tipos de salidas puede generar.
    """
    capabilities = {
        'can_upscale': False,
        'can_facedetail': False
    }
    if not isinstance(workflow_json, dict):
        return capabilities

    for node in workflow_json.values():
        if not isinstance(node, dict):
            continue
        
        class_type = node.get("class_type", "").lower()
        title = node.get("_meta", {}).get("title", "").lower()
        
        # Detección de Upscale
        if 'upscale' in class_type or 'upscale' in title or 'hires' in title:
            capabilities['can_upscale'] = True
            
        # Detección de Face Detailer (Más robusta)
        # Incluimos: face, detailer, segs (Impact Pack), sam (Segment Anything), bbox (Bounding Box)
        # NOTA: Quitamos 'sam' para evitar conflicto con 'sampler'
        face_keywords = ['face', 'detailer', 'segs', 'segmentation', 'bbox', 'impact']
        if any(k in class_type for k in face_keywords) or any(k in title for k in face_keywords):
            capabilities['can_facedetail'] = True
            
    return capabilities

def analyze_workflow(prompt_workflow):
    analysis = {
        "checkpoint": None, "vae": None, "loras": [], "width": None, "height": None,
        "seed": None, "steps": None, "cfg": None, "sampler_name": None, "scheduler": None,
        "upscale_by": None, # NUEVO CAMPO
    }
    if not isinstance(prompt_workflow, dict): return analysis
    
    # Primera pasada: Buscar nodos específicos conocidos
    for node_id, details in prompt_workflow.items():
        if not isinstance(details, dict): continue
        class_type, inputs = details.get("class_type"), details.get("inputs", {})
        title = details.get("_meta", {}).get("title", "").upper() # Convertir a mayúsculas para ser consistente
        
        if class_type == "CheckpointLoaderSimple": analysis["checkpoint"] = inputs.get("ckpt_name")
        elif class_type == "VAELoader": analysis["vae"] = inputs.get("vae_name")
        elif class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                if (lora_name := inputs.get(f"lora_{i}_name")) and lora_name.lower() != "none":
                    analysis["loras"].append({"name": lora_name, "strength": inputs.get(f"lora_{i}_strength")})
        elif class_type == "DW_resolution": analysis["width"], analysis["height"] = inputs.get("WIDTH"), inputs.get("HEIGHT")
        elif class_type == "DW_seed": analysis["seed"] = inputs.get("seed")
        elif title == "STEPS": analysis["steps"] = inputs.get("value")
        elif title == "CFG": analysis["cfg"] = inputs.get("value")
        elif title == "UPSCALER BY": analysis["upscale_by"] = inputs.get("value") # NUEVA LÍNEA
        elif class_type == "DW_SamplerSelector": analysis["sampler_name"] = inputs.get("sampler_name")
        elif class_type == "DW_SchedulerSelector": analysis["scheduler"] = inputs.get("scheduler")
        elif class_type == "EmptyLatentImage":
            if analysis["width"] is None: analysis["width"] = inputs.get("width")
            if analysis["height"] is None: analysis["height"] = inputs.get("height")
    
    # Segunda pasada: Buscar en cualquier nodo que tenga estos campos (ej. KSampler, KSamplerAdvanced)
    # Solo si no se han encontrado aún
    for node_id, details in prompt_workflow.items():
        if not isinstance(details, dict): continue
        inputs = details.get("inputs", {})
        
        if analysis["seed"] is None and "seed" in inputs and isinstance(inputs["seed"], (int, float)):
             analysis["seed"] = inputs["seed"]
        if analysis["steps"] is None and "steps" in inputs and isinstance(inputs["steps"], int):
             analysis["steps"] = inputs["steps"]
        if analysis["cfg"] is None and "cfg" in inputs and isinstance(inputs["cfg"], (int, float)):
             analysis["cfg"] = inputs["cfg"]
        if analysis["sampler_name"] is None and "sampler_name" in inputs and isinstance(inputs["sampler_name"], str):
             analysis["sampler_name"] = inputs["sampler_name"]
        if analysis["scheduler"] is None and "scheduler" in inputs and isinstance(inputs["scheduler"], str):
             analysis["scheduler"] = inputs["scheduler"]

    return analysis

def update_workflow(prompt_workflow, new_values, lora_names=None, lora_strengths=None):
    lora_names = lora_names or []
    lora_strengths = lora_strengths or []

    # --- NUEVA LÓGICA DE DETECCIÓN DE NODOS (MÁS ROBUSTA) ---
    positive_nodes = set()
    negative_nodes = set()
    
    # 1. Búsqueda por Título (Prioridad Máxima)
    for node_id, details in prompt_workflow.items():
        title = details.get("_meta", {}).get("title", "").lower()
        
        if "positive" in title:
            positive_nodes.add(node_id)
        elif "negative" in title:
            negative_nodes.add(node_id)
            
    # 2. Si no se encontraron por título, usar rastreo de conexiones
    if not positive_nodes or not negative_nodes:
        sampler_nodes = []
        for node_id, details in prompt_workflow.items():
            class_type = details.get("class_type", "")
            if "Sampler" in class_type or "sampler" in class_type.lower():
                sampler_nodes.append((node_id, details))

        def find_all_text_nodes(start_node_id):
            found_nodes = set()
            stack = [start_node_id]
            visited = set()
            while stack:
                curr_id = stack.pop()
                if curr_id in visited: continue
                visited.add(curr_id)
                node = prompt_workflow.get(curr_id)
                if not node: continue
                class_type = node.get("class_type", "")
                inputs = node.get("inputs", {})
                is_text_node = False
                if "CLIPTextEncode" in class_type: is_text_node = True
                elif "PrimitiveNode" in class_type and isinstance(inputs.get("value"), str): is_text_node = True
                else:
                    text_candidates = ["text", "text_g", "text_l", "prompt", "text_positive", "positive_prompt", "text_negative", "negative_prompt"]
                    if any(k in inputs and isinstance(inputs[k], str) for k in text_candidates): is_text_node = True
                if is_text_node: found_nodes.add(curr_id)
                for val in inputs.values():
                    if isinstance(val, list) and len(val) > 0: stack.append(val[0])
            return found_nodes

        for s_id, s_details in sampler_nodes:
            inputs = s_details.get("inputs", {})
            if not positive_nodes and (pos_link := inputs.get("positive")):
                if isinstance(pos_link, list): positive_nodes.update(find_all_text_nodes(pos_link[0]))
            if not negative_nodes and (neg_link := inputs.get("negative")):
                if isinstance(neg_link, list): negative_nodes.update(find_all_text_nodes(neg_link[0]))

    # 3. Limpieza y Corrección de Conflictos
    # Si un nodo está en ambos conjuntos, el título manda.
    # Si tiene "Positive" en el título, SE QUITA de los negativos.
    for node_id in list(negative_nodes):
        title = prompt_workflow.get(node_id, {}).get("_meta", {}).get("title", "").lower()
        if "positive" in title:
            negative_nodes.remove(node_id)
            positive_nodes.add(node_id)

    # Si tiene "Negative" en el título, SE QUITA de los positivos.
    for node_id in list(positive_nodes):
        title = prompt_workflow.get(node_id, {}).get("_meta", {}).get("title", "").lower()
        if "negative" in title:
            positive_nodes.remove(node_id)
            negative_nodes.add(node_id)

    # Identificar fuentes de parámetros numéricos
    steps_source_id, cfg_source_id, seed_source_id = None, None, None
    # (Re-escaneo de samplers para parámetros)
    sampler_nodes = []
    for node_id, details in prompt_workflow.items():
        class_type = details.get("class_type", "")
        if "Sampler" in class_type or "sampler" in class_type.lower():
            sampler_nodes.append((node_id, details))

    for s_id, s_details in sampler_nodes:
        inputs = s_details.get("inputs", {})
        if isinstance(inputs.get("steps"), list): steps_source_id = inputs["steps"][0]
        if isinstance(inputs.get("cfg"), list): cfg_source_id = inputs["cfg"][0]
        if isinstance(inputs.get("seed"), list): seed_source_id = inputs["seed"][0]

    # Actualizar valores
    for node_id, details in prompt_workflow.items():
        if not isinstance(details, dict): continue
        class_type, inputs = details.get("class_type"), details.get("inputs", {})
        title = details.get("_meta", {}).get("title", "").upper() # Convertir a mayúsculas para ser consistente
        
        # Actualizar Prompts (Soporte para Primitives y Stylers)
        candidates = ["text", "text_g", "text_l", "prompt", "text_positive", "positive_prompt", "value"]
        
        if node_id in positive_nodes and "prompt" in new_values:
            for k in candidates:
                if k in inputs: inputs[k] = new_values["prompt"]

        if node_id in negative_nodes and "negative_prompt" in new_values:
            for k in candidates:
                if k in inputs: inputs[k] = new_values["negative_prompt"]

        # Actualizar LoRAs
        if class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                inputs[f"lora_{i}_name"], inputs[f"lora_{i}_strength"] = "None", 1.0
            for i, lora_name in enumerate(lora_names[:6]):
                inputs[f"lora_{i+1}_name"] = lora_name
                if i < len(lora_strengths): inputs[f"lora_{i+1}_strength"] = float(lora_strengths[i])
        
        # Actualizar otros parámetros
        if class_type == "CheckpointLoaderSimple" and "checkpoint" in new_values: inputs["ckpt_name"] = new_values["checkpoint"]
        elif class_type == "VAELoader" and "vae" in new_values and new_values["vae"] != "None": inputs["vae_name"] = new_values["vae"]
        elif class_type == "DW_resolution" and "width" in new_values: inputs["WIDTH"], inputs["HEIGHT"] = int(new_values["width"]), int(new_values["height"])
        # ELIMINADO: DW_SamplerSelector y DW_SchedulerSelector
        elif class_type == "EmptyLatentImage" and "width" in new_values: inputs["width"], inputs["height"] = int(new_values["width"]), int(new_values["height"])

        # ELIMINADO: steps y cfg
        if node_id == seed_source_id and "seed" in new_values: inputs["seed"] = int(new_values["seed"])
        elif class_type == "DW_seed" and "seed" in new_values: inputs["seed"] = int(new_values["seed"])
        # ELIMINADO: DW_IntValue (STEPS) y DW_FloatValue (CFG)
        
        # --- NUEVO: Soporte para DW_IntValue de WIDTH y HEIGHT ---
        elif class_type == "DW_IntValue" and title == "WIDTH" and "width" in new_values: inputs["value"] = int(new_values["width"])
        elif class_type == "DW_IntValue" and title == "HEIGHT" and "height" in new_values: inputs["value"] = int(new_values["height"])
        
        # --- TURBO UPSCALER: Actualizar si existe el nodo ---
        elif class_type == "DW_FloatValue" and title == "UPSCALER BY" and "upscale_by" in new_values:
             inputs["value"] = float(new_values["upscale_by"])

        # Manejo específico para DW_KsamplerAdvanced y otros samplers
        if "Sampler" in class_type or "sampler" in class_type.lower():
             if node_id not in [steps_source_id, cfg_source_id, seed_source_id]:
                 if "seed" in new_values and "seed" in inputs and not isinstance(inputs["seed"], list): inputs["seed"] = int(new_values["seed"])
                 # ELIMINADO: steps, cfg, sampler_name, scheduler
        
        # --- PARCHE PARA DW_KsamplerAdvanced (Error 400) ---
        if class_type == "DW_KsamplerAdvanced":
            if "tiled_width" not in inputs: inputs["tiled_width"] = 512
            if "tiled_height" not in inputs: inputs["tiled_height"] = 512
            if "tiled_overlap" not in inputs: inputs["tiled_overlap"] = 32
            # --- NUEVO: Eliminar parámetro inválido ---
            if "guide_size" in inputs: del inputs["guide_size"]
            if "max_size" in inputs: del inputs["max_size"]
            
    return prompt_workflow

# --- FUNCIÓN PRINCIPAL DE GENERACIÓN ---

def classify_image_node(node_id, workflow):
    """
    Rastrea el origen de un nodo de imagen para clasificarlo.
    Retorna (nombre_clasificacion, debe_guardarse)
    """
    node = workflow.get(str(node_id))
    if not node: return "Gen_Unknown", True
    
    title = node.get("_meta", {}).get("title", "").upper() # Convertir a mayúsculas para comparar
    
    # --- CLASIFICACIÓN POR TÍTULO (FLEXIBLE) ---
    # Buscamos palabras clave en el título del nodo SaveImage
    
    if "FACE" in title or "DETAILER" in title:
        return "Gen_FaceDetailer", True
    
    if "UPSCALE" in title:
        return "Gen_UpScaler", True
        
    if "NORMAL" in title or "BASE" in title:
        return "Gen_Normal", True

    # --- Lógica anterior como fallback (si el título no dice nada claro) ---
    class_type = node.get("class_type", "").lower()
    title_lower = title.lower()

    # 2. Rastreo hacia atrás (Backtracking)
    inputs = node.get("inputs", {})
    image_source_id = None
    
    if "images" in inputs and isinstance(inputs["images"], list):
        image_source_id = inputs["images"][0]
    elif "image" in inputs and isinstance(inputs["image"], list):
        image_source_id = inputs["image"][0]
        
    if not image_source_id: return "Gen_Normal", True

    current_id = str(image_source_id)
    visited = set()
    
    while current_id and current_id not in visited:
        visited.add(current_id)
        curr_node = workflow.get(current_id)
        if not curr_node: break
        
        class_type = curr_node.get("class_type", "").lower()
        title = curr_node.get("_meta", {}).get("title", "").lower()
        
        if "face" in class_type or "detailer" in class_type or "segs" in class_type:
            return "Gen_FaceDetailer", True
        if "upscale" in class_type or "upscale" in title:
            return "Gen_UpScaler", True
        if "preview" in class_type or "preview" in title:
            return "Gen_Normal", True
            
        if "reroute" in class_type:
            inputs = curr_node.get("inputs", {})
            if "image" in inputs and isinstance(inputs["image"], list):
                current_id = str(inputs["image"][0])
                continue
            elif "images" in inputs and isinstance(inputs["images"], list):
                current_id = str(inputs["images"][0])
                continue
        
        if "decode" in class_type:
            inputs = curr_node.get("inputs", {})
            if "samples" in inputs and isinstance(inputs["samples"], list):
                current_id = str(inputs["samples"][0])
                continue
                
        if "sampler" in class_type:
            return "Gen_Normal", True
            
        break

    return "Gen_Normal", True

async def generate_image_from_character(character, user_prompt, width=None, height=None, seed=None, allowed_types=None):
    """
    Genera imágenes usando la configuración del personaje y el prompt del usuario.
    Retorna (lista_de_imagenes_bytes, prompt_id, workflow_final) o ([], None, None) si falla.
    """
    if not character.character_config:
        raise ValueError("El personaje no tiene configuración.")

    # 1. Leer Workflow Base
    @sync_to_async
    def read_workflow_file():
        with open(character.base_workflow.json_file.path, 'r', encoding='utf-8') as f:
            return json.load(f)

    prompt_workflow_base = await read_workflow_file()
    
    # 2. Preparar Configuración (Aquí se respeta lo que definió el Admin)
    character_config = json.loads(character.character_config)
    
    # --- ESTRATEGIA SÁNDWICH DE PROMPTS (CORREGIDA) ---
    # Orden: [Prefijo (Personaje)] + [Usuario] + [Sufijo (Calidad)]
    
    prefix = character.prompt_prefix if character.prompt_prefix else "" # Personaje
    suffix = character.prompt_suffix if character.prompt_suffix else "" # Calidad
    
    # Construcción limpia con comas
    parts = []
    
    # 1. Personaje (Prefijo)
    if prefix: parts.append(prefix)
    
    # 2. Usuario (Sin paréntesis forzados ni peso 1.2)
    if user_prompt: parts.append(user_prompt)
    
    # 3. Calidad (Sufijo)
    if suffix: parts.append(suffix)
    
    full_positive_prompt = ", ".join(parts)
        
    # Negativo: Solo lo que está en la BD
    full_negative_prompt = character.negative_prompt
    
    final_config = {
        **character_config, 
        'prompt': full_positive_prompt,
        'negative_prompt': full_negative_prompt
    }
    
    # Sobrescribir dimensiones si se proporcionan
    if width: final_config['width'] = width
    if height: final_config['height'] = height
    
    # --- NUEVO: Manejo de Seed desde el Cliente ---
    # Si el usuario envió una semilla válida (distinta de -1 o vacío), la usamos.
    # Si no, usamos la lógica por defecto (random o fija del config).
    
    use_random_seed = True
    
    if seed is not None and str(seed).strip() != "" and str(seed) != "-1":
        try:
            final_config['seed'] = int(seed)
            use_random_seed = False
        except ValueError:
            pass # Si no es un número válido, ignoramos y usamos random
            
    if use_random_seed:
        # Si no se forzó semilla manual, miramos la config del personaje
        if final_config.get('seed_behavior', 'random') == 'random':
            final_config['seed'] = random.randint(0, 999999999999999)
        else:
            # Si es fixed, ya debería venir en el JSON, pero nos aseguramos
            if 'seed' not in final_config:
                 final_config['seed'] = random.randint(0, 999999999999999)
    
    lora_names = final_config.pop('lora_names', [])
    lora_strengths = final_config.pop('lora_strengths', [])

    # 3. Actualizar Workflow
    updated_workflow = update_workflow(prompt_workflow_base, final_config, lora_names, lora_strengths)
    
    # 4. Conectar y Generar
    client_id = str(uuid.uuid4())
    address = await get_active_comfyui_address()
    _, ws_protocol = get_protocols(address)
    uri = f"{ws_protocol}://{address}/ws?clientId={client_id}"

    images_data = []

    async with websockets.connect(uri) as websocket:
        async with httpx.AsyncClient(timeout=300.0) as client:
            queued_prompt = await queue_prompt(client, updated_workflow, client_id, address)
            prompt_id = queued_prompt['prompt_id']
            
            while True:
                try:
                    out = await websocket.recv()
                    if isinstance(out, str):
                        message = json.loads(out)
                        if message['type'] == 'execution_error':
                            print(f"ERROR DE NODO COMFYUI: {message['data']}")
                        if message['type'] == 'executing' and message['data']['node'] is None and message['data']['prompt_id'] == prompt_id:
                            break
                except websockets.exceptions.ConnectionClosed:
                    break
            
            history = await get_history(client, prompt_id, address)
            history = history[prompt_id]
            
            for node_id in history['outputs']:
                classification, should_save = classify_image_node(node_id, updated_workflow)
                
                if not should_save:
                    continue
                
                if allowed_types is not None and classification not in allowed_types:
                    continue
                
                node_output = history['outputs'][node_id]
                if 'images' in node_output:
                    for image in node_output['images']:
                        image_bytes = await get_image(client, image['filename'], image['subfolder'], image['type'], address)
                        if image_bytes:
                            images_data.append((image_bytes, classification))
    
    return images_data, prompt_id, updated_workflow
# Limpieza final
