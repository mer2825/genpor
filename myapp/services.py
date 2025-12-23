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
    response = await client.post(f"{protocol}://{address}/prompt", json=p)
    response.raise_for_status()
    return response.json()

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

def analyze_workflow(prompt_workflow):
    analysis = {
        "checkpoint": None, "vae": None, "loras": [], "width": None, "height": None,
        "seed": None, "steps": None, "cfg": None, "sampler_name": None, "scheduler": None,
    }
    if not isinstance(prompt_workflow, dict): return analysis
    for node_id, details in prompt_workflow.items():
        if not isinstance(details, dict): continue
        class_type, inputs = details.get("class_type"), details.get("inputs", {})
        if class_type == "CheckpointLoaderSimple": analysis["checkpoint"] = inputs.get("ckpt_name")
        elif class_type == "VAELoader": analysis["vae"] = inputs.get("vae_name")
        elif class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                if (lora_name := inputs.get(f"lora_{i}_name")) and lora_name.lower() != "none":
                    analysis["loras"].append({"name": lora_name, "strength": inputs.get(f"lora_{i}_strength")})
        elif class_type == "DW_resolution": analysis["width"], analysis["height"] = inputs.get("WIDTH"), inputs.get("HEIGHT")
        elif class_type == "DW_seed": analysis["seed"] = inputs.get("seed")
        elif class_type == "DW_IntValue" and details.get("_meta", {}).get("title") == "STEPS": analysis["steps"] = inputs.get("value")
        elif class_type == "DW_FloatValue" and details.get("_meta", {}).get("title") == "CFG": analysis["cfg"] = inputs.get("value")
        elif class_type == "DW_SamplerSelector": analysis["sampler_name"] = inputs.get("sampler_name")
        elif class_type == "DW_SchedulerSelector": analysis["scheduler"] = inputs.get("scheduler")
        elif class_type == "EmptyLatentImage":
            if analysis["width"] is None: analysis["width"] = inputs.get("width")
            if analysis["height"] is None: analysis["height"] = inputs.get("height")
        elif class_type == "KSampler":
            if analysis["seed"] is None: analysis["seed"] = inputs.get("seed")
            if analysis["steps"] is None: analysis["steps"] = inputs.get("steps")
            if analysis["cfg"] is None: analysis["cfg"] = inputs.get("cfg")
            if analysis["sampler_name"] is None: analysis["sampler_name"] = inputs.get("sampler_name")
            if analysis["scheduler"] is None: analysis["scheduler"] = inputs.get("scheduler")
    return analysis

def update_workflow(prompt_workflow, new_values, lora_names=None, lora_strengths=None):
    lora_names = lora_names or []
    lora_strengths = lora_strengths or []

    positive_node_id, negative_node_id = None, None
    sampler_nodes = []
    for node_id, details in prompt_workflow.items():
        if "Sampler" in details.get("class_type", ""):
            sampler_nodes.append((node_id, details))

    text_node_types = ["CLIPTextEncode", "CLIPTextEncodeSDXL", "CLIPTextEncodeSDXLRefiner"]
    
    # Limpieza de prompts
    for node_id, details in prompt_workflow.items():
        if details.get("class_type") in text_node_types:
            inputs = details.get("inputs", {})
            if "text" in inputs: inputs["text"] = ""
            if "text_g" in inputs: inputs["text_g"] = ""
            if "text_l" in inputs: inputs["text_l"] = ""

    # Identificar nodos positivos/negativos
    for node_id, details in prompt_workflow.items():
        if details.get("class_type") in text_node_types:
            for s_id, s_details in sampler_nodes:
                if s_details.get("inputs", {}).get("positive", [None])[0] == node_id: positive_node_id = node_id
                if s_details.get("inputs", {}).get("negative", [None])[0] == node_id: negative_node_id = node_id

    steps_source_id, cfg_source_id, seed_source_id = None, None, None
    for s_id, s_details in sampler_nodes:
        inputs = s_details.get("inputs", {})
        if isinstance(inputs.get("steps"), list): steps_source_id = inputs["steps"][0]
        if isinstance(inputs.get("cfg"), list): cfg_source_id = inputs["cfg"][0]
        if isinstance(inputs.get("seed"), list): seed_source_id = inputs["seed"][0]

    for node_id, details in prompt_workflow.items():
        if not isinstance(details, dict): continue
        class_type, inputs = details.get("class_type"), details.get("inputs", {})
        
        if node_id == positive_node_id and "prompt" in new_values:
            if "text_g" in inputs: inputs["text_g"] = inputs["text_l"] = new_values["prompt"]
            else: inputs["text"] = new_values["prompt"]
                
        if node_id == negative_node_id and "negative_prompt" in new_values:
            if "text_g" in inputs: inputs["text_g"] = inputs["text_l"] = new_values["negative_prompt"]
            else: inputs["text"] = new_values["negative_prompt"]

        if class_type == "DW_LoRAStackApplySimple":
            for i in range(1, 7):
                inputs[f"lora_{i}_name"], inputs[f"lora_{i}_strength"] = "None", 1.0
            for i, lora_name in enumerate(lora_names[:6]):
                inputs[f"lora_{i+1}_name"] = lora_name
                if i < len(lora_strengths): inputs[f"lora_{i+1}_strength"] = float(lora_strengths[i])
        
        if class_type == "CheckpointLoaderSimple" and "checkpoint" in new_values: inputs["ckpt_name"] = new_values["checkpoint"]
        elif class_type == "VAELoader" and "vae" in new_values and new_values["vae"] != "None": inputs["vae_name"] = new_values["vae"]
        elif class_type == "DW_resolution" and "width" in new_values: inputs["WIDTH"], inputs["HEIGHT"] = int(new_values["width"]), int(new_values["height"])
        elif class_type == "DW_SamplerSelector" and "sampler_name" in new_values: inputs["sampler_name"] = new_values["sampler_name"]
        elif class_type == "DW_SchedulerSelector" and "scheduler" in new_values: inputs["scheduler"] = new_values["scheduler"]
        elif class_type == "EmptyLatentImage" and "width" in new_values: inputs["width"], inputs["height"] = int(new_values["width"]), int(new_values["height"])

        if node_id == steps_source_id and "steps" in new_values: inputs["value"] = int(new_values["steps"])
        elif node_id == cfg_source_id and "cfg" in new_values: inputs["value"] = float(new_values["cfg"])
        elif node_id == seed_source_id and "seed" in new_values: inputs["seed"] = int(new_values["seed"])
        elif class_type == "DW_seed" and "seed" in new_values: inputs["seed"] = int(new_values["seed"])
        elif class_type == "DW_IntValue" and details.get("_meta", {}).get("title") == "STEPS" and "steps" in new_values: inputs["value"] = int(new_values["steps"])
        elif class_type == "DW_FloatValue" and details.get("_meta", {}).get("title") == "CFG" and "cfg" in new_values: inputs["value"] = float(new_values["cfg"])
        elif "Sampler" in class_type and node_id not in [steps_source_id, cfg_source_id, seed_source_id]:
             if "seed" in new_values and "seed" in inputs and not isinstance(inputs["seed"], list): inputs["seed"] = int(new_values["seed"])
             if "steps" in new_values and "steps" in inputs and not isinstance(inputs["steps"], list): inputs["steps"] = int(new_values["steps"])
             if "cfg" in new_values and "cfg" in inputs and not isinstance(inputs["cfg"], list): inputs["cfg"] = float(new_values["cfg"])
             if "sampler_name" in new_values and "sampler_name" in inputs: inputs["sampler_name"] = new_values["sampler_name"]
             if "scheduler" in new_values and "scheduler" in inputs: inputs["scheduler"] = new_values["scheduler"]
            
    return prompt_workflow

# --- FUNCIÓN PRINCIPAL DE GENERACIÓN ---

async def generate_image_from_character(character, user_prompt):
    """
    Genera una imagen usando la configuración del personaje y el prompt del usuario.
    Retorna (image_bytes, prompt_id) o (None, None) si falla.
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
    
    full_positive_prompt = f"{user_prompt}, {character.positive_prompt}" if character.positive_prompt else user_prompt
    
    final_config = {
        **character_config, 
        'prompt': full_positive_prompt,
        'negative_prompt': character.negative_prompt
    }
    
    # Manejo de Seed
    if final_config.get('seed_behavior', 'random') == 'random':
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

    print(f"DEBUG: Conectando a WebSocket {uri}")

    async with websockets.connect(uri) as websocket:
        async with httpx.AsyncClient(timeout=300.0) as client:
            queued_prompt = await queue_prompt(client, updated_workflow, client_id, address)
            prompt_id = queued_prompt['prompt_id']
            
            print(f"DEBUG: Prompt enviado. ID: {prompt_id}")

            while True:
                out = await websocket.recv()
                if isinstance(out, str):
                    message = json.loads(out)
                    if message['type'] == 'executing' and message['data']['node'] is None and message['data']['prompt_id'] == prompt_id:
                        break
            
            history = await get_history(client, prompt_id, address)
            history = history[prompt_id]
            
            for node_id in history['outputs']:
                node_output = history['outputs'][node_id]
                if 'images' in node_output:
                    for image in node_output['images']:
                        image_bytes = await get_image(client, image['filename'], image['subfolder'], image['type'], address)
                        if image_bytes:
                            return image_bytes, prompt_id
    
    return None, None
