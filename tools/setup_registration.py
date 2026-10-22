"""Registration used by installers; preserve locally edited presets on re-install."""
import json
import os

from voicebench.service import Service


class RegistrationService(Service):
    def upsert_engine(self, payload):
        if os.environ.get('VOICEBENCH_PRESERVE_ENGINES') == '1':
            existing = self.db.fetch_one('SELECT * FROM engines WHERE slug=?', (payload['slug'],))
            if existing:
                existing['config'] = json.loads(existing.pop('config_json'))
                return existing
        gpu = os.environ.get('VOICEBENCH_SETUP_GPU')
        if gpu is not None:
            payload.setdefault('config', {}).setdefault('environment', {}).update(
                CUDA_DEVICE_ORDER='PCI_BUS_ID', CUDA_VISIBLE_DEVICES=gpu)
        return super().upsert_engine(payload)
