How to run this projects:

llama.cpp:
1. Download and set up llama.cpp
https://github.com/ggml-org/llama.cpp
2. Build llama.ccp
3. Make sure llama-cli is in the path.

pyhon:
1. create new .venv
2. create .env and set HF_TOKEN="<your_token>"
3. pip install -r requirements.txt
4. run src/LLMServer/start_llm_server.py 
5. run src/ContextManager/run_context_manager.py
6. run scripts/main.py
7. In browser, open http://127.0.0.1:8000

