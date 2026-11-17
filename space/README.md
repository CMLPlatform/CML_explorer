---
title: CML research assistant
emoji: 🔎
colorFrom: green
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Answers questions about CML research, with citations
---

# CML research assistant

This Space runs the research assistant of the Institute of Environmental Sciences (CML), Leiden University.
The CML explorer dashboard (https://cmlplatform.github.io/CML_explorer/) uses it for its **Ask** view.

The assistant searches titles, abstracts and keywords of papers by current CML staff (from OpenAlex) and public staff profiles.
It sends this text and the question to the language model that the Space settings name.

Source and deployment: https://github.com/CMLPlatform/CML_explorer (`deploy_space.py`).
Do not edit the files here. `deploy_space.py` replaces them.
