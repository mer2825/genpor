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
    for node in workflow_json.values():
        if not isinstance(node, dict): continue
        class_type = node.get("class_type", "").lower()
        title = node.get("_meta", {}).get("title", "").lower()
        inputs = node.get("inputs", {})
        if 'upscale' in class_type or 'hires' in title or 'resize' in class_type:
            capabilities['can_upscale'] = True
        if ('face' in title or 'face' in inputs.get("prompt", "")) and 'eye' not in title:
            capabilities['can_facedetail'] = True
        if 'eye' in title or 'eye' in inputs.get("prompt", ""):
            capabilities['can_eyedetailer'] = True
    return capabilities

def analyze_workflow(prompt_workflow):
    analysis = {"checkpoint": None, "vae": None, "loras": [], "width": None, "height": None, "seed": None, "steps": None, "cfg": None, "sampler_name": None, "scheduler": None, "upscale_by": None, "black_list_tags": None, "promp_detailers": None, "negative_prompt": None, "promp_character": None, "enable_blacklist": True}
    if not isinstance(prompt_workflow, dict): return analysis
    for node_id, details in prompt_workflow.items():
        if not isinstance(details, dict): continue
        class_type, inputs = details.get("class_type"), details.get("inputs", {})
        title = details.get("_meta", {}).get("title", "").upper()
        if class_type == "CheckpointLoaderSimple": analysis["checkpoint"] = inputs.get("ckpt_name")
        elif class_type == "VAELoader": analysis["vae"] = inputs.get("vae_name")
        elif class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                if (lora_name := inputs.get(f"lora_{i}_name")) and lora_name.lower() != "none":
                    analysis["loras"].append({"name": lora_name, "strength": inputs.get(f"lora_{i}_strength")})
        elif class_type == "DW_resolution":
            analysis["width"], analysis["height"] = inputs.get("WIDTH"), inputs.get("HEIGHT")
            analysis["upscale_by"] = inputs.get("UPSCALER")
        elif class_type == "DW_seed": analysis["seed"] = inputs.get("seed")
        elif class_type == "EmptyLatentImage":
            if analysis["width"] is None: analysis["width"] = inputs.get("width")
            if analysis["height"] is None: analysis["height"] = inputs.get("height")
        elif title == "BLACK_LIST_TAGS":
            analysis["black_list_tags"] = inputs.get("text")
        elif title == "PROMP_DETAILERS":
            analysis["promp_detailers"] = inputs.get("text")
        elif title == "NEGATIVE PROMP":
            analysis["negative_prompt"] = inputs.get("text")
        elif title == "PROMP_CHARACTER":
            analysis["promp_character"] = inputs.get("text")
            
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
    prompt_workflow[dummy_whitelist_id] = {
        "inputs": {"text": ""},
        "class_type": "DW_Text",
        "_meta": {"title": "AUTO_GENERATED_WHITELIST"}
    }
    # ---------------------------------------------

    for node_id, details in prompt_workflow.items():
        title = details.get("_meta", {}).get("title", "").lower()
        if "positive" in title: positive_nodes.add(node_id)
        elif "negative" in title: negative_nodes.add(node_id)
    if not positive_nodes or not negative_nodes:
        sampler_nodes = [ (n_id, n) for n_id, n in prompt_workflow.items() if "sampler" in n.get("class_type", "").lower() ]
        # ... (omitted for brevity, assume it works)
    for node_id, details in prompt_workflow.items():
        if not isinstance(details, dict): continue
        class_type, inputs = details.get("class_type"), details.get("inputs", {})
        title = details.get("_meta", {}).get("title", "").upper()
        
        # --- AUTO-FIX: CONNECT WHITELIST IF MISSING ---
        if class_type == "DW_Ultimate_Blacklist_Filter":
            if "WHITELIST" not in inputs:
                inputs["WHITELIST"] = [dummy_whitelist_id, 0]
        # ----------------------------------------------

        candidates = ["text", "text_g", "text_l", "prompt", "value"]
        if node_id in positive_nodes and "prompt" in new_values:
            for k in candidates:
                if k in inputs:
                    # --- FIX: DO NOT OVERWRITE LINKS ---
                    # If the input is a list (e.g. ["22", 0]), it's a connection. Don't break it.
                    if not isinstance(inputs[k], list):
                        inputs[k] = new_values["prompt"]
                    # -----------------------------------
        if node_id in negative_nodes and "negative_prompt" in new_values:
            for k in candidates:
                if k in inputs:
                    # --- FIX: DO NOT OVERWRITE LINKS ---
                    if not isinstance(inputs[k], list):
                        inputs[k] = new_values["negative_prompt"]
                    # -----------------------------------
        if title == "BLACK_LIST_TAGS":
            if "enable_blacklist" in new_values and not new_values["enable_blacklist"]:
                inputs["text"] = "" # Vaciar si está desactivado
            elif "black_list_tags" in new_values:
                inputs["text"] = new_values["black_list_tags"]
        if title == "PROMP_DETAILERS" and "promp_detailers" in new_values:
            inputs["text"] = new_values["promp_detailers"]
        if title == "NEGATIVE PROMP" and "negative_prompt" in new_values:
            inputs["text"] = new_values["negative_prompt"]
        if title == "PROMP_CHARACTER" and "promp_character" in new_values:
            inputs["text"] = new_values["promp_character"]
        if title == "PROMP_USUARIO" and "prompt" in new_values:
            inputs["text"] = new_values["prompt"]
            
        if class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                inputs[f"lora_{i}_name"], inputs[f"lora_{i}_strength"] = "None", 1.0
            for i, lora_name in enumerate(lora_names[:6]):
                inputs[f"lora_{i+1}_name"] = lora_name
                if i < len(lora_strengths):
                    try:
                        inputs[f"lora_{i+1}_strength"] = float(lora_strengths[i])
                    except (ValueError, TypeError):
                        inputs[f"lora_{i+1}_strength"] = 1.0
        if class_type == "CheckpointLoaderSimple" and "checkpoint" in new_values:
            inputs["ckpt_name"] = new_values["checkpoint"]
        elif class_type == "VAELoader" and "vae" in new_values and new_values["vae"] != "None": inputs["vae_name"] = new_values["vae"]
        elif class_type == "DW_resolution":
            if "width" in new_values:
                try: inputs["WIDTH"] = int(new_values["width"])
                except (ValueError, TypeError): pass
            if "height" in new_values:
                try: inputs["HEIGHT"] = int(new_values["height"])
                except (ValueError, TypeError): pass
            if "upscale_by" in new_values:
                try: inputs["UPSCALER"] = float(new_values["upscale_by"])
                except (ValueError, TypeError): pass
        elif class_type == "EmptyLatentImage" and "width" in new_values:
            try: inputs["width"], inputs["height"] = int(new_values["width"]), int(new_values["height"])
            except (ValueError, TypeError): pass
        elif class_type == "DW_seed" and "seed" in new_values:
            try: inputs["seed"] = int(new_values["seed"])
            except (ValueError, TypeError): pass
        if "sampler" in class_type.lower():
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
    sampler_nodes = {nid: n for nid, n in workflow.items() if "sampler" in n.get("class_type", "").lower()}
    for sampler_id, sampler_node in sampler_nodes.items():
        inputs = sampler_node.get("inputs", {})
        if "mask" in inputs and isinstance(inputs["mask"], list):
            mask_source_id = inputs["mask"][0]
            mask_node = workflow.get(mask_source_id, {})
            if mask_node and "SAM" in mask_node.get("class_type", ""):
                mask_prompt = mask_node.get("inputs", {}).get("prompt", "")
                if "eye" in mask_prompt: stage_map["Gen_EyeDetailer"] = sampler_id
                elif "face" in mask_prompt: stage_map["Gen_FaceDetailer"] = sampler_id
    for sampler_id, sampler_node in sampler_nodes.items():
        if sampler_id in stage_map.values(): continue
        inputs = sampler_node.get("inputs", {})
        is_upscaler = False
        for input_name in ["latent_image", "image"]:
            if input_name in inputs and isinstance(inputs[input_name], list):
                source_node = workflow.get(inputs[input_name][0], {})
                if source_node and "Resize" in source_node.get("class_type", ""):
                    stage_map["Gen_UpScaler"] = sampler_id
                    is_upscaler = True
                    break
        if not is_upscaler and "latent_image" not in inputs and "image" not in inputs:
             stage_map["Gen_Normal"] = sampler_id
    return stage_map

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
        for node_id, node in updated_workflow.items():
            if node.get("_meta", {}).get("title", "").upper() == "FINAL_IMAGE":
                final_output_node_id = node_id
                if "images" in node.get("inputs", {}):
                    candidate_id = node["inputs"]["images"][0]
                    candidate_node = updated_workflow.get(candidate_id, {})
                    if "Blacklist_Filter" in candidate_node.get("class_type", ""):
                        filter_node_id = candidate_id
                        if "INPUT_STRING" in candidate_node.get("inputs", {}):
                            tagger_node_id = candidate_node["inputs"]["INPUT_STRING"][0]
                break
        
        if target_sampler_id and final_output_node_id and filter_node_id:
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