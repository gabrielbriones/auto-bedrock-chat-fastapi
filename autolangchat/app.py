from fastapi import FastAPI

from autolangchat import add_autolangchat

# Create FastAPI app (existing app approach)
app = FastAPI()
add_autolangchat(app)
