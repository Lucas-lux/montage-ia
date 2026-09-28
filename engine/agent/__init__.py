"""Montage IA piloté par un agent (Claude Code, Codex, Cursor…).

L'agent parle au serveur MCP (`mcp.py`, lancé par `MontageIA.exe --mcp`), qui
relaie ses outils au moteur de l'application (`api.py`, routes `/api/agent`).
Le moteur fait le travail — transcription, coupes, sous-titres, rendu — sur
les mêmes projets que le studio : on voit le montage se construire dans
l'application, et on peut le reprendre à la main.

    edit.py      opérations de montage (mêmes règles que studio/model.js)
    service.py   ce que font les outils, sur un projet timeline
    images.py    recherche et import d'images libres (Openverse, Wikimedia, Pexels)
    visuals.py   visuels dessinés en HTML/CSS, rendus en PNG par Edge ou Chrome
    preview.py   aperçu basse définition et planche d'images du montage
    api.py       routes HTTP `/api/agent/...`
    mcp.py       serveur MCP (stdio), bibliothèque standard seulement
"""
