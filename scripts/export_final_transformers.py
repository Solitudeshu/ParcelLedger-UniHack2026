"""Package verified final checkpoints alongside, never over, legacy/interim models."""
import json
import shutil
from pathlib import Path

from ml.inference.transformer import bundle_identity, file_sha


def main():
    audit = json.loads(Path('artifacts/evaluation/final-training-integrity.json').read_bytes())
    if audit['status'] != 'PASS':
        raise ValueError('FINAL_CHECKPOINT_AUDIT_REQUIRED')
    for candidate in ('c2','c3'):
        source = Path(f'artifacts/kaggle/transformer-{candidate}-s04-v1/training')
        card = json.loads((source/'model-card.json').read_bytes())
        checked = audit['candidates'][candidate]
        if card['epochs_completed'] != 30 or file_sha(source/'best.pt') != checked['best_sha256']:
            raise ValueError('FINAL_CHECKPOINT_CHANGED')
        folder = Path(f'artifacts/models/candidates/transformer-v2/{candidate}-final-v1')
        folder.mkdir(parents=True, exist_ok=True)
        target = folder/'model.pt'
        if target.exists() and file_sha(target) != checked['best_sha256']:
            raise ValueError('REFUSE_OVERWRITING_OTHER_CHECKPOINT')
        shutil.copyfile(source/'best.pt', target)
        identity_keys = ('config','source_hashes','manifest_sha256','pair_version','backbone_revision','backbone_sha256','source_kind')
        packaged = {'model_version': f'parcel-transformer-{candidate}-final-v1', 'mode': 'SHADOW_ONLY',
                    'training_epochs_completed': 30, 'selected_epoch': checked['selected_epoch'],
                    'checkpoint_sha256': checked['best_sha256'], 'identity': {k:card[k] for k in identity_keys},
                    'source_training_card_sha256': file_sha(source/'model-card.json'),
                    'real_box_validation': 'NOT_RUN', 'production_promotion': 'HOLD',
                    'limitations': card['limitations']+['Parcel3D renders are academic-only; local research package.',
                        'Tiled inference and real-image matching need separate validation.',
                        'No automatic liability, compensation, settlement or report confirmation.']}
        (folder/'model-card.json').write_text(json.dumps(packaged,indent=2),encoding='utf-8')
        print(json.dumps({'candidate':candidate,'folder':str(folder),**bundle_identity(folder)}))


if __name__ == '__main__':
    main()
