"""Correção da demonstração do CP 03: a credencial vem do ambiente, nunca do código."""

import os

AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
