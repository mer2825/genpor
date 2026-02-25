import json
import uuid
import random
import httpx
import websockets
import asyncio
from asgiref.sync import sync_to_async
from .models import ConnectionConfig

# --- FUNCIONES DE CONFIGURACIÓN Y RED ---

def get_protocols(address):
    """Determina si usar HTTP/WS o HTTPS/WSS basado en la dirección."""
    if "runpod.net" in address or "cloudflare" in address or "ngrok" in address:
        return "https", "wss"
    return "http", "ws"

def get_active_configs_sync():
    """Helper síncrono para obtener configs de la DB."""
    return list(ConnectionConfig.objects.filter(is_active=True))

async def check_gpu_load(client, config):
    """
    Consulta la API de ComfyUI para ver cuántos trabajos tiene en cola.
    Retorna: (address, total_jobs)
    Si falla la conexión, retorna (address, 9999) para que sea la última opción.
    """
    address = config.base_url.rstrip('/')
    protocol, _ = get_protocols(address)

    # Headers para evitar bloqueo de ngrok
    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "MyApp/1.0"}

    try:
        # Timeout corto (2s) para no ralentizar al usuario si una GPU está caída
        response = await client.get(f"{protocol}://{address}/queue", headers=headers, timeout=2.0)
        if response.status_code == 200:
            data = response.json()
            # Sumamos los que se están ejecutando + los pendientes
            running = len(data.get('queue_running', []))
            pending = len(data.get('queue_pending', []))
            total_load = running + pending
            return (address, total_load)
    except Exception:
        pass

    return (address, 9999) # Penalización máxima si falla

async def get_active_comfyui_address():
    """
    Obtiene la dirección de ComfyUI más libre (Smart Load Balancing).
    Consulta en tiempo real la cola de todas las instancias activas.
    """
    configs = await sync_to_async(get_active_configs_sync)()

    if not configs:
        return "127.0.0.1:8188"

    # Si solo hay uno, no perdemos tiempo chequeando
    if len(configs) == 1:
        return configs[0].base_url.rstrip('/')

    # Chequeo en paralelo de todas las GPUs
    async with httpx.AsyncClient() as client:
        tasks = [check_gpu_load(client, config) for config in configs]
        results = await asyncio.gather(*tasks)

    # Ordenamos por carga (menor a mayor)
    # results es una lista de tuplas: [('url1', 0), ('url2', 5), ('url3', 9999)]
    results.sort(key=lambda x: x[1])

    best_address, load = results[0]

    # Si incluso el mejor tiene error (9999), devolvemos el primero de la DB por defecto
    if load == 9999:
        return configs[0].base_url.rstrip('/')

    return best_address

# --- FUNCIONES DE API COMFYUI ---

async def get_comfyui_object_info(address):
    protocol, _ = get_protocols(address)
    # Headers para evitar bloqueo de ngrok
    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "MyApp/1.0"}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{protocol}://{address}/object_info", headers=headers)
            response.raise_for_status()
            data = response.json()

            # --- MEJORA: Búsqueda más amplia de modelos ---
            checkpoints = []
            if "CheckpointLoaderSimple" in data:
                checkpoints.extend(data["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0])
            if "UNETLoader" in data:
                checkpoints.extend(data["UNETLoader"]["input"]["required"]["unet_name"][0])
            checkpoints = list(set(checkpoints))

            vaes = data.get("VAELoader", {}).get("input", {}).get("required", {}).get("vae_name", [[]])[0]

            loras = []
            if "LoraLoader" in data:
                loras.extend(data["LoraLoader"]["input"]["required"]["lora_name"][0])
            if "LoraLoaderModelOnly" in data:
                loras.extend(data["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0])
            loras = list(set(loras))

            samplers = data.get("KSampler", {}).get("input", {}).get("required", {}).get("sampler_name", [[]])[0]
            schedulers = data.get("KSampler", {}).get("input", {}).get("required", {}).get("scheduler", [[]])[0]

            return {
                "checkpoints": checkpoints,
                "vaes": vaes,
                "loras": loras,
                "samplers": samplers,
                "schedulers": schedulers,
            }
    except Exception as e:
        print(f"ERROR in get_comfyui_object_info: {e}")
        return {"checkpoints": [], "vaes": [], "loras": [], "samplers": [], "schedulers": []}

async def queue_prompt(client, prompt_workflow, client_id, address):
    protocol, _ = get_protocols(address)
    p = {"prompt": prompt_workflow, "client_id": client_id}

    try:
        response = await client.post(f"{protocol}://{address}/prompt", json=p)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        error_msg = f"ComfyUI Error {e.response.status_code}: {e.response.text}"
        print(error_msg)
        raise Exception(error_msg)

async def get_image(client, filename, subfolder, folder_type, address):
    protocol, _ = get_protocols(address)
    params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    try:
        response = await client.get(f"{protocol}://{address}/view", params=params)
        response.raise_for_status()
        return response.content
    except httpx.HTTPStatusError as e:
        print(f"ERROR DESCARGANDO IMAGEN: {e.response.status_code} - {e.response.text}")
        print(f"URL INTENTADA: {e.request.url}")
        return None
    except Exception as e:
        print(f"ERROR DE CONEXIÓN AL DESCARGAR IMAGEN: {e}")
        return None

async def get_history(client, prompt_id, address):
    protocol, _ = get_protocols(address)
    response = await client.get(f"{protocol}://{address}/history/{prompt_id}")
    response.raise_for_status()
    return response.json()

# --- LÓGICA DE WORKFLOW ---

def analyze_workflow_outputs(workflow_json):
    capabilities = {'can_upscale': False, 'can_facedetail': False, 'can_eyedetailer': False}
    if not isinstance(workflow_json, dict): return capabilities
    
    # Detectar formato
    iterator = workflow_json.values()
    if "nodes" in workflow_json and isinstance(workflow_json["nodes"], list):
        iterator = workflow_json["nodes"]

    for node in iterator:
        if not isinstance(node, dict): continue
        
        # Normalizar acceso a campos
        class_type = node.get("class_type", node.get("type", "")).lower()
        title = node.get("_meta", {}).get("title", node.get("title", "")).lower()
        
        # Construir un string con todo el contenido de texto relevante del nodo
        node_text_content = f"{title} {class_type}"
        
        # Agregar valores de inputs (si es dict)
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict):
            for val in inputs.values():
                if isinstance(val, str):
                    node_text_content += " " + val.lower()
        
        # Agregar valores de widgets (si existen)
        widgets = node.get("widgets_values", [])
        if isinstance(widgets, list):
            for val in widgets:
                if isinstance(val, str):
                    node_text_content += " " + val.lower()
        
        # Lógica de detección más agresiva
        if 'upscale' in node_text_content or 'hires' in node_text_content or 'resize' in class_type:
            capabilities['can_upscale'] = True
        
        if 'face' in node_text_content and 'eye' not in node_text_content:
            capabilities['can_facedetail'] = True
            
        if 'eye' in node_text_content:
            capabilities['can_eyedetailer'] = True
            
    return capabilities

def analyze_workflow(prompt_workflow):
    analysis = {"checkpoint": None, "vae": None, "loras": [], "width": None, "height": None, "seed": None, "steps": None, "cfg": None, "sampler_name": None, "scheduler": None, "upscale_by": None, "black_list_tags": None, "promp_detailers": None, "negative_prompt": None, "promp_character": None, "enable_blacklist": True}
    if not isinstance(prompt_workflow, dict): return analysis
    
    # --- DETECCIÓN DE FORMATO (API vs EDITOR) ---
    is_api_format = True
    if "nodes" in prompt_workflow and isinstance(prompt_workflow["nodes"], list):
        is_api_format = False
        iterator = prompt_workflow["nodes"] # Lista de dicts
    else:
        iterator = prompt_workflow.items() # Dict items (id, details)

    for item in iterator:
        if is_api_format:
            node_id, details = item
            if not isinstance(details, dict): continue
            class_type = details.get("class_type")
            inputs = details.get("inputs", {})
            title = details.get("_meta", {}).get("title", "").upper()
            widgets_values = details.get("widgets_values", [])
        else:
            # Editor Format
            details = item
            class_type = details.get("type")
            inputs = details.get("inputs", {}) # En editor esto suele ser lista de slots
            title = details.get("title", "").upper()
            widgets_values = details.get("widgets_values", [])

        # --- HELPER PARA OBTENER TEXTO ---
        def get_text_value():
            # 1. Intentar obtener de inputs (Solo API format tiene valores aquí usualmente)
            if isinstance(inputs, dict):
                val = inputs.get("text")
                if isinstance(val, str) and val: return val
            
            # 2. Intentar obtener de widgets_values
            if isinstance(widgets_values, list) and len(widgets_values) > 0:
                if isinstance(widgets_values[0], str): return widgets_values[0]
            return None
        # ---------------------------------

        if class_type == "CheckpointLoaderSimple": 
            if isinstance(inputs, dict): analysis["checkpoint"] = inputs.get("ckpt_name")
            # En editor format, ckpt_name suele estar en widgets_values[0]
            elif not is_api_format and widgets_values: analysis["checkpoint"] = widgets_values[0]

        elif class_type == "VAELoader": 
            if isinstance(inputs, dict): analysis["vae"] = inputs.get("vae_name")
            elif not is_api_format and widgets_values: analysis["vae"] = widgets_values[0]

        elif class_type == "DW_LoRAStackApplySimple":
            # Esto es complejo de mapear en Editor format por posición, asumimos API por ahora para LoRAs complejos
            if is_api_format:
                for i in range(1, 7):
                    if (lora_name := inputs.get(f"lora_{i}_name")) and lora_name.lower() != "none":
                        analysis["loras"].append({"name": lora_name, "strength": inputs.get(f"lora_{i}_strength")})
        
        elif class_type == "DW_resolution":
            if is_api_format:
                analysis["width"], analysis["height"] = inputs.get("WIDTH"), inputs.get("HEIGHT")
                analysis["upscale_by"] = inputs.get("UPSCALER")
            elif widgets_values and len(widgets_values) >= 3:
                # Asumimos orden: width, height, upscale (basado en el JSON visto)
                analysis["width"] = widgets_values[0]
                analysis["height"] = widgets_values[1]
                analysis["upscale_by"] = widgets_values[2]

        elif class_type == "DW_seed": 
            if is_api_format: analysis["seed"] = inputs.get("seed")
            elif widgets_values: analysis["seed"] = widgets_values[0]

        elif class_type == "EmptyLatentImage":
            if is_api_format:
                if analysis["width"] is None: analysis["width"] = inputs.get("width")
                if analysis["height"] is None: analysis["height"] = inputs.get("height")
            elif widgets_values and len(widgets_values) >= 2:
                if analysis["width"] is None: analysis["width"] = widgets_values[0]
                if analysis["height"] is None: analysis["height"] = widgets_values[1]
        
        # --- NODOS DE TEXTO ---
        elif title == "BLACK_LIST_TAGS":
            analysis["black_list_tags"] = get_text_value()
        elif title == "PROMP_DETAILERS":
            analysis["promp_detailers"] = get_text_value()
        elif title == "NEGATIVE PROMP":
            analysis["negative_prompt"] = get_text_value()
        elif title == "PROMP_CHARACTER":
            analysis["promp_character"] = get_text_value()
            
    # Segunda pasada para samplers (solo API format es fiable para esto por ahora)
    if is_api_format:
        for node_id, details in prompt_workflow.items():
            if not isinstance(details, dict): continue
            inputs = details.get("inputs", {})
            if analysis["seed"] is None and "seed" in inputs and isinstance(inputs["seed"], (int, float)): analysis["seed"] = inputs["seed"]
            if analysis["steps"] is None and "steps" in inputs and isinstance(inputs["steps"], int): analysis["steps"] = inputs["steps"]
            if analysis["cfg"] is None and "cfg" in inputs and isinstance(inputs["cfg"], (int, float)): analysis["cfg"] = inputs["cfg"]
            if analysis["sampler_name"] is None and "sampler_name" in inputs and isinstance(inputs["sampler_name"], str): analysis["sampler_name"] = inputs["sampler_name"]
            if analysis["scheduler"] is None and "scheduler" in inputs and isinstance(inputs["scheduler"], str): analysis["scheduler"] = inputs["scheduler"]
    
    return analysis

def update_workflow(prompt_workflow, new_values, lora_names=None, lora_strengths=None):
    lora_names = lora_names or []
    lora_strengths = lora_strengths or []
    positive_nodes, negative_nodes = set(), set()
    
    # --- AUTO-FIX: INJECT DUMMY WHITELIST NODE ---
    dummy_whitelist_id = "99999"
    # Solo inyectar si es API format (dict de nodos)
    is_api_format = not ("nodes" in prompt_workflow and isinstance(prompt_workflow["nodes"], list))
    
    if is_api_format:
        prompt_workflow[dummy_whitelist_id] = {
            "inputs": {"text": ""},
            "class_type": "DW_Text",
            "_meta": {"title": "AUTO_GENERATED_WHITELIST"}
        }
    # ---------------------------------------------

    # Iterador agnóstico
    if is_api_format:
        iterator = prompt_workflow.items()
    else:
        # Si es editor format, no podemos editar fácilmente sin romperlo o convertirlo.
        # Por ahora, intentamos editar in-place si encontramos los nodos por título.
        iterator = enumerate(prompt_workflow["nodes"]) # index, dict

    # Detección de nodos positivos/negativos
    for item in iterator:
        if is_api_format:
            node_id, details = item
            title = details.get("_meta", {}).get("title", "").lower()
        else:
            node_id, details = item # node_id es index aquí
            title = details.get("title", "").lower()
            
        if "positive" in title: positive_nodes.add(node_id)
        elif "negative" in title: negative_nodes.add(node_id)

    # Reiniciar iterador
    if is_api_format:
        iterator = prompt_workflow.items()
    else:
        iterator = enumerate(prompt_workflow["nodes"])

    for item in iterator:
        if is_api_format:
            node_id, details = item
        else:
            node_id, details = item # node_id es index

        if not isinstance(details, dict): continue
        
        class_type = details.get("class_type") if is_api_format else details.get("type")
        inputs = details.get("inputs", {})
        title = details.get("_meta", {}).get("title", "").upper() if is_api_format else details.get("title", "").upper()
        widgets_values = details.get("widgets_values", [])

        # --- AUTO-FIX: CONNECT WHITELIST IF MISSING (Solo API) ---
        if is_api_format and class_type == "DW_Ultimate_Blacklist_Filter":
            if "WHITELIST" not in inputs:
                inputs["WHITELIST"] = [dummy_whitelist_id, 0]
        # ----------------------------------------------

        # Helper para actualizar texto (API o Editor)
        def update_text(val):
            if is_api_format:
                # API: Actualizar inputs
                candidates = ["text", "text_g", "text_l", "prompt", "value"]
                for k in candidates:
                    if k in inputs and not isinstance(inputs[k], list):
                        inputs[k] = val
                        return # Actualizado
                # Si no encontró, quizás es un nodo custom que usa widgets_values incluso en API
                if widgets_values: widgets_values[0] = val
            else:
                # Editor: Actualizar widgets_values
                if widgets_values:
                    widgets_values[0] = val

        candidates = ["text", "text_g", "text_l", "prompt", "value"]
        
        # Actualización de Prompts Positivos/Negativos genéricos
        if node_id in positive_nodes and "prompt" in new_values:
            update_text(new_values["prompt"])
        if node_id in negative_nodes and "negative_prompt" in new_values:
            update_text(new_values["negative_prompt"])

        # Actualización por Título Específico
        if title == "BLACK_LIST_TAGS":
            if "enable_blacklist" in new_values and not new_values["enable_blacklist"]:
                update_text("")
            elif "black_list_tags" in new_values:
                update_text(new_values["black_list_tags"])
        
        if title == "PROMP_DETAILERS" and "promp_detailers" in new_values:
            update_text(new_values["promp_detailers"])
        
        if title == "NEGATIVE PROMP" and "negative_prompt" in new_values:
            update_text(new_values["negative_prompt"])
        
        if title == "PROMP_CHARACTER" and "promp_character" in new_values:
            update_text(new_values["promp_character"])
        
        if title == "PROMP_USUARIO" and "prompt" in new_values:
            update_text(new_values["prompt"])

        # Actualización de LoRAs (Solo API soportado robustamente)
        if is_api_format and class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                inputs[f"lora_{i}_name"], inputs[f"lora_{i}_strength"] = "None", 1.0
            for i, lora_name in enumerate(lora_names[:6]):
                inputs[f"lora_{i+1}_name"] = lora_name
                if i < len(lora_strengths):
                    try:
                        inputs[f"lora_{i+1}_strength"] = float(lora_strengths[i])
                    except (ValueError, TypeError):
                        inputs[f"lora_{i+1}_strength"] = 1.0
        
        # Actualización de Checkpoint (API y Editor básico)
        if class_type == "CheckpointLoaderSimple" and "checkpoint" in new_values:
            if is_api_format: inputs["ckpt_name"] = new_values["checkpoint"]
            elif widgets_values: widgets_values[0] = new_values["checkpoint"]
            
        elif class_type == "VAELoader" and "vae" in new_values and new_values["vae"] != "None": 
            if is_api_format: inputs["vae_name"] = new_values["vae"]
            elif widgets_values: widgets_values[0] = new_values["vae"]

        # Actualización de Resolución y Seed (API y Editor básico)
        elif class_type == "DW_resolution":
            w, h, up = None, None, None
            if "width" in new_values: 
                try: w = int(new_values["width"])
                except (ValueError, TypeError): pass
            if "height" in new_values: 
                try: h = int(new_values["height"])
                except (ValueError, TypeError): pass
            if "upscale_by" in new_values: 
                try: up = float(new_values["upscale_by"])
                except (ValueError, TypeError): pass
            
            if is_api_format:
                if w is not None: inputs["WIDTH"] = w
                if h is not None: inputs["HEIGHT"] = h
                if up is not None: inputs["UPSCALER"] = up
            elif widgets_values and len(widgets_values) >= 3:
                if w is not None: widgets_values[0] = w
                if h is not None: widgets_values[1] = h
                if up is not None: widgets_values[2] = up

        elif class_type == "EmptyLatentImage" and "width" in new_values:
            try:
                w, h = int(new_values["width"]), int(new_values["height"])
                if is_api_format:
                    inputs["width"], inputs["height"] = w, h
                elif widgets_values and len(widgets_values) >= 2:
                    widgets_values[0], widgets_values[1] = w, h
            except (ValueError, TypeError): pass

        elif class_type == "DW_seed" and "seed" in new_values:
            try:
                s = int(new_values["seed"])
                if is_api_format: inputs["seed"] = s
                elif widgets_values: widgets_values[0] = s
            except (ValueError, TypeError): pass

        if is_api_format and "sampler" in class_type.lower():
            if "seed" in new_values and "seed" in inputs and not isinstance(inputs["seed"], list):
                try: inputs["seed"] = int(new_values["seed"])
                except (ValueError, TypeError): pass

    return prompt_workflow

def find_dependencies(workflow, start_node_id):
    nodes_to_keep = set()
    queue = [start_node_id]
    while queue:
        current_id = queue.pop(0)
        if current_id in nodes_to_keep: continue
        nodes_to_keep.add(current_id)
        node = workflow.get(current_id)
        if node and 'inputs' in node:
            for value in node['inputs'].values():
                # --- MEJORA: Robustez para IDs enteros o strings ---
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int)):
                    dependency_id = str(value[0]) # Convertir siempre a string para consistencia
                    if dependency_id not in nodes_to_keep:
                        queue.append(dependency_id)
    return nodes_to_keep

def map_workflow_stages(workflow):
    stage_map = {}
    
    # --- DETECCIÓN DE FORMATO (API vs EDITOR) ---
    is_api_format = True
    if "nodes" in workflow and isinstance(workflow["nodes"], list):
        is_api_format = False
        iterator = workflow["nodes"] # Lista de dicts
    else:
        iterator = workflow.items() # Dict items (id, details)

    # Recolectar nodos sampler
    sampler_nodes = {}
    for item in iterator:
        if is_api_format:
            node_id, details = item
        else:
            details = item # item es el dict directamente en lista
            node_id = str(details.get("id", "unknown")) # Usar ID interno si existe

        if not isinstance(details, dict): continue
        
        class_type = details.get("class_type") if is_api_format else details.get("type")
        if class_type and "sampler" in class_type.lower():
            sampler_nodes[node_id] = details

    # Mapear etapas
    for sampler_id, sampler_node in sampler_nodes.items():
        inputs = sampler_node.get("inputs", {})
        
        # Detectar Face/Eye Detailer por máscara
        if "mask" in inputs and isinstance(inputs["mask"], list):
            mask_source_id = str(inputs["mask"][0])
            
            # Buscar nodo de máscara
            mask_node = None
            if is_api_format:
                mask_node = workflow.get(mask_source_id)
            else:
                # En formato editor es difícil buscar por ID de enlace sin un mapa previo
                # Por simplicidad, asumimos API format para lógica compleja de enlaces
                pass 

            if mask_node:
                class_type = mask_node.get("class_type", "")
                if "SAM" in class_type:
                    # Intentar obtener prompt del nodo SAM
                    mask_prompt = ""
                    if "prompt" in mask_node.get("inputs", {}):
                        mask_prompt = mask_node["inputs"]["prompt"]
                    elif "widgets_values" in mask_node:
                         # Asumir primer widget es el prompt
                         mask_prompt = str(mask_node["widgets_values"][0])
                    
                    if "eye" in mask_prompt.lower(): stage_map["Gen_EyeDetailer"] = sampler_id
                    elif "face" in mask_prompt.lower(): stage_map["Gen_FaceDetailer"] = sampler_id

    # Segunda pasada para Upscaler y Normal
    for sampler_id, sampler_node in sampler_nodes.items():
        if sampler_id in stage_map.values(): continue
        
        inputs = sampler_node.get("inputs", {})
        is_upscaler = False
        
        for input_name in ["latent_image", "image"]:
            if input_name in inputs and isinstance(inputs[input_name], list):
                source_id = str(inputs[input_name][0])
                
                source_node = None
                if is_api_format:
                    source_node = workflow.get(source_id)
                
                if source_node:
                    class_type = source_node.get("class_type", "")
                    if "Resize" in class_type or "Upscale" in class_type:
                        stage_map["Gen_UpScaler"] = sampler_id
                        is_upscaler = True
                        break
        
        if not is_upscaler and "latent_image" not in inputs and "image" not in inputs:
             stage_map["Gen_Normal"] = sampler_id

    return stage_map

def convert_editor_to_api_format(editor_workflow):
    """
    Convierte un workflow en formato Editor (lista de nodos) a formato API (diccionario de IDs).
    Esta es una conversión simplificada y puede necesitar ajustes según la complejidad.
    """
    api_workflow = {}
    
    if not isinstance(editor_workflow, dict) or "nodes" not in editor_workflow:
        return editor_workflow # Ya es API o desconocido
        
    nodes = editor_workflow["nodes"]
    links = editor_workflow.get("links", [])
    
    # Mapa de Link ID -> (Node ID, Slot Index)
    link_map = {}
    for link in links:
        # link format: [id, origin_id, origin_slot, target_id, target_slot, type]
        if len(link) >= 4:
            link_id = link[0]
            origin_node_id = str(link[1])
            origin_slot = link[2]
            link_map[link_id] = (origin_node_id, origin_slot)

    for node in nodes:
        node_id = str(node["id"])
        class_type = node["type"]
        
        # Construir inputs
        inputs = {}
        
        # 1. Inputs desde widgets_values (si aplica)
        widgets = node.get("widgets_values", [])
        
        # Mapeo específico por tipo de nodo (basado en errores comunes y estructura estándar)
        if class_type == "DW_KsamplerAdvanced":
            # Orden inferido: steps, cfg, sampler_name, scheduler, denoise, seed_mode, seed, ...
            # Error pide: sampler_name, last_step, cfg, force_full_denoise, scheduler, group_mask_islands, noise_mask_feather, batch_size, crop_factor, disable_noise, force_inpaint, steps, denoise, noise_mask, start_step
            # Esto es complejo. Asumimos un mapeo básico si es posible, o usamos valores por defecto.
            if len(widgets) >= 4:
                inputs["steps"] = widgets[0]
                inputs["cfg"] = widgets[1]
                inputs["sampler_name"] = widgets[2]
                inputs["scheduler"] = widgets[3]
                # ... faltan muchos ...
                # Rellenar con defaults para evitar error 400 si faltan
                inputs.setdefault("denoise", 1.0)
                inputs.setdefault("start_step", 0)
                inputs.setdefault("last_step", 200) # FIX: Max 200
                inputs.setdefault("force_full_denoise", True)
                inputs.setdefault("group_mask_islands", True)
                inputs.setdefault("noise_mask_feather", 0)
                inputs.setdefault("batch_size", 1)
                inputs.setdefault("crop_factor", 1.0) # FIX: Min 1.0
                inputs.setdefault("disable_noise", False)
                inputs.setdefault("force_inpaint", True)
                inputs.setdefault("noise_mask", None) # FIX: Explicit None for missing mask
                
        elif class_type == "DW_SAM3Segmentation":
            if len(widgets) >= 1: inputs["prompt"] = widgets[0]
            inputs.setdefault("threshold", 0.3)
            inputs.setdefault("mask_blur", 0)
            inputs.setdefault("min_height_pixels", 0)
            inputs.setdefault("min_width_pixels", 0)
            inputs.setdefault("keep_model_loaded", False)
            inputs.setdefault("mask_expand", 0)
            inputs.setdefault("use_video_model", False)
            
        elif class_type == "DW_WD14_Tagger_V3":
            if len(widgets) >= 1: inputs["model"] = widgets[0]
            inputs.setdefault("threshold", 0.35)
            inputs.setdefault("character_threshold", 0.85)
            inputs.setdefault("exclude_tags", "")
            inputs.setdefault("extension", "png")
            inputs.setdefault("suffix_tag", "")
            inputs.setdefault("replace_underscore", True)
            inputs.setdefault("save_image", False)
            inputs.setdefault("trailing_comma", True)
            inputs.setdefault("prefix_tag", "")
            inputs.setdefault("sort_by", "alphabetical")
            inputs.setdefault("filename_prefix", "ComfyUI")
            inputs.setdefault("output_folder", "output")
            
        elif class_type == "DW_resolution":
            if len(widgets) >= 3:
                inputs["WIDTH"] = widgets[0]
                inputs["HEIGHT"] = widgets[1]
                inputs["UPSCALER"] = widgets[2]
            inputs.setdefault("SUM", 0) # Error específico visto
            
        elif class_type == "DW_TextConcatenate":
             if len(widgets) >= 3:
                 inputs["text_1"] = widgets[0] # A veces es text_1, text_2...
                 # ...
             inputs.setdefault("connector", " ")
             inputs.setdefault("text_4", "")
             inputs.setdefault("normalize_commas", True)
             
        elif class_type == "DW_ResizeLongestSide":
            inputs.setdefault("divisible_by", 8)
            inputs.setdefault("method", "LANCZOS") # FIX: Uppercase
             
        elif class_type == "CLIPSetLastLayer":
            if len(widgets) >= 1: inputs["stop_at_clip_layer"] = widgets[0]
            
        elif class_type == "DW_JPGPreview":
            inputs.setdefault("quality", 95)

        # Mapeos genéricos anteriores
        elif class_type == "CheckpointLoaderSimple":
            if len(widgets) > 0: inputs["ckpt_name"] = widgets[0]
        elif class_type == "VAELoader":
            if len(widgets) > 0: inputs["vae_name"] = widgets[0]
        elif class_type == "EmptyLatentImage":
            if len(widgets) >= 2:
                inputs["width"] = widgets[0]
                inputs["height"] = widgets[1]
        elif class_type == "CLIPTextEncode" or class_type == "DW_Text":
             if len(widgets) > 0: inputs["text"] = widgets[0]

        # 2. Inputs desde conexiones (links)
        if "inputs" in node:
            for input_def in node["inputs"]:
                name = input_def["name"]
                link_id = input_def["link"]
                if link_id is not None and link_id in link_map:
                    origin_node, origin_slot = link_map[link_id]
                    inputs[name] = [origin_node, origin_slot]

        api_workflow[node_id] = {
            "class_type": class_type,
            "inputs": inputs,
            "_meta": {
                "title": node.get("title", class_type) # FIX: Use class_type as fallback if title missing
            }
        }
        
    return api_workflow

async def generate_image_from_character(character, user_prompt, width=None, height=None, seed=None, allowed_types=None, checkpoint=None, lora_strength=None):
    if not character.character_config:
        # Si no tiene config propia, intentamos usar la del workflow base
        if character.base_workflow.active_config:
            character_config = json.loads(character.base_workflow.active_config)
        else:
            raise ValueError("El personaje no tiene configuración y el workflow base tampoco.")
    else:
        # Si tiene config propia, la cargamos
        character_config = json.loads(character.character_config)

        # Y mezclamos con la del workflow base para rellenar huecos (Herencia)
        if character.base_workflow.active_config:
            base_config = json.loads(character.base_workflow.active_config)
            # La config del personaje tiene prioridad, así que actualizamos la base con ella
            final_mixed_config = base_config.copy()
            final_mixed_config.update(character_config)
            character_config = final_mixed_config

    @sync_to_async
    def read_workflow_file():
        with open(character.base_workflow.json_file.path, 'r', encoding='utf-8') as f:
            return json.load(f)

    prompt_workflow_base = await read_workflow_file()
    
    # --- CONVERSIÓN AUTOMÁTICA A FORMATO API SI ES NECESARIO ---
    if "nodes" in prompt_workflow_base and isinstance(prompt_workflow_base["nodes"], list):
        print("DETECTED EDITOR FORMAT. ATTEMPTING CONVERSION TO API FORMAT...")
        prompt_workflow_base = convert_editor_to_api_format(prompt_workflow_base)

    # Ya no usamos prompt_prefix/suffix del modelo Character porque los borramos.
    # El prompt del usuario va directo a PROMP_USUARIO (manejado en update_workflow)

    final_config = {**character_config, 'prompt': user_prompt}

    if width: final_config['width'] = width
    if height: final_config['height'] = height
    if checkpoint: final_config['checkpoint'] = checkpoint

    if seed is None or str(seed).strip() in ["", "-1"]:
        final_config['seed'] = random.randint(0, 2147483647)
    else:
        final_config['seed'] = int(seed) % 2147483647

    address = await get_active_comfyui_address()
    available_models = await get_comfyui_object_info(address)
    available_checkpoints = available_models.get("checkpoints", [])
    selected_checkpoint = final_config.get('checkpoint')
    if selected_checkpoint and selected_checkpoint not in available_checkpoints:
        print(f"WARNING: Checkpoint '{selected_checkpoint}' not found on server {address}. Falling back to workflow default.")
        del final_config['checkpoint']

    lora_names = final_config.pop('lora_names', [])
    lora_strengths = final_config.pop('lora_strengths', [])
    if lora_strength is not None and lora_strengths:
        try: lora_strengths[0] = float(lora_strength)
        except (ValueError, IndexError): pass

    updated_workflow = update_workflow(prompt_workflow_base, final_config, lora_names, lora_strengths)

    if allowed_types:
        target_classification = allowed_types[-1]
        stage_map = map_workflow_stages(updated_workflow)
        target_sampler_id = stage_map.get(target_classification)

        final_output_node_id, filter_node_id, tagger_node_id = None, None, None
        
        # --- DETECCIÓN DE FORMATO (API vs EDITOR) ---
        is_api_format = True
        if "nodes" in updated_workflow and isinstance(updated_workflow["nodes"], list):
            is_api_format = False
            iterator = updated_workflow["nodes"] # Lista de dicts
        else:
            iterator = updated_workflow.items() # Dict items (id, details)

        for item in iterator:
            if is_api_format:
                node_id, node = item
            else:
                node = item
                node_id = str(node.get("id", "unknown"))

            if not isinstance(node, dict): continue
            
            title = node.get("_meta", {}).get("title", "").upper() if is_api_format else node.get("title", "").upper()
            
            if title == "FINAL_IMAGE":
                final_output_node_id = node_id
                if "images" in node.get("inputs", {}):
                    candidate_id = node["inputs"]["images"][0]
                    
                    # Buscar nodo candidato
                    candidate_node = None
                    if is_api_format:
                        candidate_node = updated_workflow.get(candidate_id, {})
                    else:
                        # En formato editor es difícil buscar por ID sin mapa
                        pass 

                    if candidate_node and "Blacklist_Filter" in candidate_node.get("class_type", ""):
                        filter_node_id = candidate_id
                        if "INPUT_STRING" in candidate_node.get("inputs", {}):
                            tagger_node_id = candidate_node["inputs"]["INPUT_STRING"][0]
                break

        if target_sampler_id and final_output_node_id and filter_node_id and is_api_format:
            print(f"OPTIMIZATION: Target is '{target_classification}'. Rewiring filter and tagger to sampler '{target_sampler_id}'.")
            updated_workflow[filter_node_id]["inputs"]["image"] = [target_sampler_id, 5]
            if tagger_node_id and tagger_node_id in updated_workflow:
                updated_workflow[tagger_node_id]["inputs"]["image"] = [target_sampler_id, 5]

            required_nodes = find_dependencies(updated_workflow, final_output_node_id)

            # --- FIX: FORCE KEEP PROMPT NODES ---
            # Even if dependency tracing fails, we MUST keep these nodes for the record
            titles_to_keep = ["PROMP_CHARACTER", "PROMP_USUARIO", "PROMP_DETAILERS", "NEGATIVE PROMP", "BLACK_LIST_TAGS"]
            for nid, node in updated_workflow.items():
                # Convert title to UPPER to match case-insensitive
                if node.get("_meta", {}).get("title", "").upper() in titles_to_keep:
                    required_nodes.add(nid)
            # ------------------------------------

            updated_workflow = {nid: updated_workflow[nid] for nid in required_nodes}
            print(f"OPTIMIZATION: Pruned workflow to {len(updated_workflow)} nodes.")
        else:
            print(f"WARNING: Could not prune for '{target_classification}'. (Sampler: {target_sampler_id}, Output: {final_output_node_id}, Filter: {filter_node_id})")

    client_id = str(uuid.uuid4())
    _, ws_protocol = get_protocols(address)
    uri = f"{ws_protocol}://{address}/ws?clientId={client_id}"
    images_data = []
    headers = {"ngrok-skip-browser-warning": "true", "User-Agent": "MyApp/1.0"}
    target_classification = allowed_types[-1] if allowed_types else "Gen_Normal"

    async with websockets.connect(uri) as websocket:
        async with httpx.AsyncClient(timeout=600.0, headers=headers) as client:
            queued_prompt = await queue_prompt(client, updated_workflow, client_id, address)
            prompt_id = queued_prompt['prompt_id']
            while True:
                try:
                    out = await websocket.recv()
                    if isinstance(out, str):
                        message = json.loads(out)
                        if message['type'] == 'execution_error':
                            print(f"ERROR DE NODO COMFYUI: {message['data']}")
                        if message['type'] == 'executing' and message['data']['node'] is None:
                            break
                except websockets.exceptions.ConnectionClosed:
                    break
            history = await get_history(client, prompt_id, address)
            history = history[prompt_id]
            for node_id, node_output in history['outputs'].items():
                if node_output.get('images'):
                    image = node_output['images'][0]
                    image_bytes = await get_image(client, image['filename'], image['subfolder'], image['type'], address)
                    if image_bytes:
                        images_data.append((image_bytes, target_classification))
                        break
    return images_data, prompt_id, updated_workflow