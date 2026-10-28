"""One-time quieter BGM setting; old frozen artistic plans remain untouched."""
from copy import deepcopy

REVISION='quieter_bgm_v1'

def upgrade_settings(document):
    result=deepcopy(document)
    nodes=[result]+list(result.get('editing_profiles',{}).get('items',{}).values())
    for node in nodes:
        if node.get('bgm_balance_revision')!=REVISION:
            node['bgm_trim_db']=min(float(node.get('bgm_trim_db',0)),-12.)
            node['bgm_balance_revision']=REVISION
    return result
