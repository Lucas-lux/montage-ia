"""Boîte à outils : petits utilitaires qui marchent sans projet de montage.

Chaque outil est un module autonome (fonctions pures + appel ffmpeg) que le
serveur expose sous `/api/tools/...` et qu'on peut aussi lancer en ligne de
commande (`python -m engine.tools.<outil>`).
"""
