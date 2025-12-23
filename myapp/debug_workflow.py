import os
import sys
import django
import json

# Configuración de Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from myapp.models import Character

def inspect_ana_workflow():
    try:
        ana = Character.objects.get(name='Ana')
        workflow_path = ana.base_workflow.json_file.path
        print(f"Analizando workflow de Ana: {workflow_path}")
        
        with open(workflow_path, 'r', encoding='utf-8') as f:
            workflow = json.load(f)
            
        print("\n--- DETALLES DEL NODO DW_KsamplerAdvanced (ID 29) ---")
        if "29" in workflow:
            node = workflow["29"]
            print(json.dumps(node, indent=4))
        else:
            print("ERROR: El nodo 29 no existe en el workflow.")

    except Character.DoesNotExist:
        print("El personaje 'Ana' no existe.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    inspect_ana_workflow()
