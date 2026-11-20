"""Generisches ComfyUI-API-Prompt-Mapping, ohne Abhängigkeit vom 3D-Modell."""
from __future__ import annotations
import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path


INPUT_CLASSES = ('LoadImage', 'LoadImageFromPath', 'LoadImageFromFile')
OUTPUT_CLASSES = ('SaveGLB', 'Save3DAdvanced', 'Save3D', 'SaveMesh', 'Save3DModel')


@dataclass(frozen=True)
class NodeOption:
    id: str
    label: str


class Workflow:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = json.loads(self.path.read_text(encoding='utf-8-sig'))
        if isinstance(raw, dict) and isinstance(raw.get('prompt'), dict):
            raw = raw['prompt']
        if (not isinstance(raw, dict) or not raw or
                not all(isinstance(v, dict) and isinstance(v.get('inputs'), dict)
                        and isinstance(v.get('class_type'), str) for v in raw.values())):
            raise ValueError('Bitte einen ComfyUI-Workflow im API-Format exportieren (Save API Format). '
                             'Das normale UI-Workflow-JSON ist nicht kompatibel.')
        self.graph = raw

    def nodes(self) -> list[NodeOption]:
        return [NodeOption(str(k), f'{k} · {v.get("_meta", {}).get("title") or v["class_type"]}')
                for k, v in self.graph.items()]

    def image_options(self) -> list[NodeOption]:
        return [n for n in self.nodes() if self.graph[n.id]['class_type'] in INPUT_CLASSES
                or ('image' in self.graph[n.id]['inputs'] and
                    isinstance(self.graph[n.id]['inputs']['image'], str))]

    def output_options(self) -> list[NodeOption]:
        known = [n for n in self.nodes() if self.graph[n.id]['class_type'] in OUTPUT_CLASSES
                 or (self.graph[n.id]['class_type'].lower().startswith('save') and
                     any(x in self.graph[n.id]['class_type'].lower() for x in ('mesh', '3d', 'glb')))]
        return known or [n for n in self.nodes() if self.graph[n.id]['class_type'].lower().startswith('save')]

    def resolve(self, image_node='', output_node='', image_input='image', prefix_input='filename_prefix'):
        options = self.image_options()
        outputs = self.output_options()
        if not image_node and len(options) == 1:
            image_node = options[0].id
        if not output_node and len(outputs) == 1:
            output_node = outputs[0].id
        if image_node not in self.graph or image_input not in self.graph[image_node]['inputs']:
            raise ValueError('Bild-Node nicht eindeutig: im Tab Workflow einen Image-Input wählen.')
        if output_node not in self.graph:
            raise ValueError('GLB-Export-Node nicht eindeutig: im Tab Workflow den Export-Node wählen.')
        # A valid connected graph should actually use the selected image to produce the GLB.
        # If there are no links at all (unusual custom workflow), allow manual mapping.
        def parents(node_id):
            item = self.graph.get(str(node_id), {})
            for val in item.get('inputs', {}).values():
                if (isinstance(val, list) and len(val) == 2 and
                        str(val[0]) in self.graph and isinstance(val[1], int)):
                    yield str(val[0])
        seen = set()
        def reaches(node_id):
            if node_id == str(image_node):
                return True
            if node_id in seen:
                return False
            seen.add(node_id)
            return any(reaches(p) for p in parents(node_id))
        if any(True for item in self.graph.values() for val in item['inputs'].values()
               if isinstance(val, list) and len(val) == 2 and str(val[0]) in self.graph):
            if not reaches(str(output_node)):
                raise ValueError('Der ausgewählte Bild-Node ist nicht mit dem GLB-Export verbunden. '
                                 'Bitte Workflow-Verkabelung und Zuordnung prüfen.')
        return str(image_node), str(output_node)

    def build(self, upload_name: str, prompt_id: str, image_node: str,
              output_node: str, image_input='image', prefix_input='filename_prefix') -> dict:
        image_node, output_node = self.resolve(image_node, output_node, image_input, prefix_input)
        if not upload_name or '\\' in upload_name or '/' in upload_name or upload_name in ('.', '..'):
            raise ValueError('Ungültiger Upload-Dateiname.')
        if not re.fullmatch(r'[a-f0-9\-]{36}', prompt_id):
            raise ValueError('Ungültige ComfyUI-Auftrags-ID.')
        graph = copy.deepcopy(self.graph)
        graph[image_node]['inputs'][image_input] = upload_name
        # Eindeutige Namen verhindern Kollisionen mit vorhandenen ComfyUI-Ausgaben.
        if prefix_input and prefix_input in graph[output_node]['inputs']:
            graph[output_node]['inputs'][prefix_input] = 'AssetFoundry/' + prompt_id
        return graph
