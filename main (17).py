import os
import asyncio
import json
import subprocess
import threading
import uvicorn
import requests
import discord
from discord.ext import commands, tasks
from fastapi import FastAPI, Request, HTTPException, Form, Cookie
from fastapi.responses import (StreamingResponse, HTMLResponse,
                               FileResponse, PlainTextResponse,
                               RedirectResponse)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import time
import uuid
import sqlite3
from datetime import datetime

#########################################
# CRIA A PASTA /db SE NÃO EXISTIR
#########################################
os.makedirs('/home/container/db', exist_ok=True)

#########################################
# CONFIGURAÇÕES DE PASTAS
#########################################

VIDEO_FOLDER = "/home/container/filmes/"
IMAGENS_FOLDER = "/home/container/imagens/"
TRANSCODED_FOLDER = "/home/container/transcoded/"  # Pasta para salvar MP4 transcodificados
LEGENDAS_FOLDER = "/home/container/legendas/"       # Pasta para legendas

os.makedirs(VIDEO_FOLDER, exist_ok=True)
os.makedirs(IMAGENS_FOLDER, exist_ok=True)
os.makedirs(TRANSCODED_FOLDER, exist_ok=True)
os.makedirs(LEGENDAS_FOLDER, exist_ok=True)

#########################################
# INICIALIZAÇÃO DO FASTAPI
#########################################

video_app = FastAPI()

video_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

video_app.mount("/imagens", StaticFiles(directory=IMAGENS_FOLDER), name="imagens")
video_app.mount("/static", StaticFiles(directory="static"), name="static")
video_app.mount("/legendas", StaticFiles(directory=LEGENDAS_FOLDER), name="legendas")

#########################################
# BANCO DE DADOS SQLITE PARA USUÁRIOS
#########################################

DB_PATH = "/home/container/db/boteco_users.db"

def init_db():
    """
    Cria a tabela 'users' se não existir, incluindo a coluna created_at.
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0,
            admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """)
        conn.commit()

def create_user(username: str, password: str):
    """
    Cria um novo usuário no DB com approved=0 e admin=0;
    registra a data/hora de criação (created_at).
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
        INSERT INTO users (username, password, approved, admin, created_at)
        VALUES (?, ?, 0, 0, ?)
        """, (username, password, datetime.now().isoformat()))
        conn.commit()

def get_user(username: str):
    """
    Retorna as informações do usuário (username, password, approved, admin, created_at)
    ou None se não encontrado.
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("""
        SELECT username, password, approved, admin, created_at
        FROM users
        WHERE username = ?
        """, (username,))
        row = c.fetchone()
        if row:
            return {
                "username": row[0],
                "password": row[1],
                "approved": bool(row[2]),
                "admin": bool(row[3]),
                "created_at": row[4]
            }
        return None

def set_approved(username: str, approved: bool):
    """
    Define a coluna 'approved' de um usuário no DB.
    """
    val = 1 if approved else 0
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET approved=? WHERE username=?", (val, username))
        conn.commit()

def delete_user(username: str):
    """
    Remove completamente o usuário do banco de dados.
    """
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE username=?", (username,))
        conn.commit()

def set_admin(username: str, is_admin: bool):
    """
    Define a coluna 'admin' de um usuário no DB.
    """
    val = 1 if is_admin else 0
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET admin=? WHERE username=?", (val, username))
        conn.commit()

# Inicializa a tabela de usuários
init_db()

#########################################
# AUTENTICAÇÃO / SESSÃO / APROVAÇÃO DE USUÁRIOS
#########################################

SESSION_COOKIE_NAME = "session_id"

# Dicionário em memória: session_id -> username
active_sessions = {}

def is_admin(username: str) -> bool:
    """
    Verifica se é o admin 'eletriom' ou se o usuário tem flag 'admin' = True no DB.
    """
    if username == "eletriom":
        return True
    user_data = get_user(username)
    if user_data and user_data["admin"]:
        return True
    return False

def is_approved_user(username: str) -> bool:
    """
    Verifica se o usuário é admin ou está aprovado no DB.
    """
    if is_admin(username):
        return True
    user_data = get_user(username)
    if user_data and user_data["approved"]:
        return True
    return False

def create_session(username: str):
    """
    Cria um session_id único e associa ao username em 'active_sessions'.
    """
    session_id = str(uuid.uuid4())
    active_sessions[session_id] = username
    return session_id

def get_current_username_from_session(session_id: str):
    """
    Retorna o username associado a este session_id, ou None se inválido.
    """
    return active_sessions.get(session_id)

#########################################
# FUNÇÕES DISCORD: APROVAÇÃO DE USUÁRIOS
#########################################

import discord
from discord.ext import commands, tasks

class ApproveDenyView(discord.ui.View):
    def __init__(self, username: str):
        super().__init__(timeout=None)  # sem timeout
        self.username = username

    @discord.ui.button(label="Aprovar", style=discord.ButtonStyle.green)
    async def approve_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        # Se o usuário existe, aprova
        user_data = get_user(self.username)
        if user_data:
            set_approved(self.username, True)
            await interaction.response.send_message(
                f"Usuário **{self.username}** aprovado!",
                ephemeral=True
            )
            await interaction.message.edit(
                content=f"Usuário {self.username} foi **aprovado**!",
                view=None
            )
        else:
            await interaction.response.send_message(
                f"Usuário **{self.username}** não existe mais.",
                ephemeral=True
            )
            await interaction.message.edit(
                content=f"Usuário {self.username} não encontrado no sistema.",
                view=None
            )

    @discord.ui.button(label="Negar", style=discord.ButtonStyle.red)
    async def deny_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        # Se o usuário existe, remove do DB
        user_data = get_user(self.username)
        if user_data:
            delete_user(self.username)
            await interaction.response.send_message(
                f"Usuário **{self.username}** removido/negado!",
                ephemeral=True
            )
            await interaction.message.edit(
                content=f"Usuário {self.username} foi **negado/removido**!",
                view=None
            )
        else:
            await interaction.response.send_message(
                f"Usuário **{self.username}** não existe mais.",
                ephemeral=True
            )
            await interaction.message.edit(
                content=f"Usuário {self.username} não encontrado no sistema.",
                view=None
            )

async def send_approval_request(username: str, bot: commands.Bot, channel_id: int):
    """
    Envia a mensagem de pedido de aprovação para o canal especificado,
    com botões Aprovar/Negar.
    """
    channel = bot.get_channel(channel_id)
    if channel is not None:
        view = ApproveDenyView(username)
        await channel.send(
            content=f"Usuário **{username}** se registrou no Filmes do Boteco e aguarda aprovação:",
            view=view
        )
    else:
        print(f"[Aviso] Canal {channel_id} não encontrado para aprovação.")

def submit_approval_request(username: str, bot: commands.Bot, channel_id: int):
    """
    Chama a função assíncrona de envio de mensagem de aprovação via 'run_coroutine_threadsafe'.
    """
    asyncio.run_coroutine_threadsafe(
        send_approval_request(username, bot, channel_id),
        bot.loop
    )

#########################################
# ENDPOINTS DE AUTENTICAÇÃO
#########################################

@video_app.get("/login", response_class=HTMLResponse)
def login_page():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Login - Filmes do Boteco</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

            body {
                font-family: 'Roboto', sans-serif;
                background: linear-gradient(135deg, #71b7e6, #9b59b6);
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .container {
                background: rgba(255, 255, 255, 0.1);
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                backdrop-filter: blur(8.5px);
                -webkit-backdrop-filter: blur(8.5px);
                border: 1px solid rgba(255, 255, 255, 0.18);
                width: 350px;
                animation: fadeIn 1s ease-in-out;
            }
            h1 {
                text-align: center;
                color: #fff;
                margin-bottom: 30px;
            }
            label {
                display: block;
                margin-bottom: 5px;
                color: #fff;
            }
            input[type="text"],
            input[type="password"] {
                width: 100%;
                padding: 10px;
                margin-bottom: 20px;
                border: none;
                border-radius: 8px;
                outline: none;
                transition: box-shadow 0.3s;
            }
            input[type="text"]:focus,
            input[type="password"]:focus {
                box-shadow: 0 0 10px rgba(255, 255, 255, 0.7);
            }
            button {
                width: 100%;
                padding: 10px;
                border: none;
                border-radius: 8px;
                background-color: #2980b9;
                color: #fff;
                font-weight: bold;
                cursor: pointer;
                transition: background-color 0.3s;
            }
            button:hover {
                background-color: #3498db;
            }
            .links {
                text-align: center;
                margin-top: 15px;
            }
            a {
                color: #ecf0f1;
                text-decoration: none;
                font-weight: bold;
            }
            a:hover {
                color: #bdc3c7;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(-50px); }
                to { opacity: 1; transform: translateY(0); }
            }
        </style>
    </head>
    <body>
    <div class="container">
        <h1>Login</h1>
        <form method="post" action="/login">
            <label for="username">Usuário:</label>
            <input type="text" id="username" name="username" required>

            <label for="password">Senha:</label>
            <input type="password" id="password" name="password" required>

            <button type="submit">Entrar</button>
        </form>
        <div class="links">
            <p>Ainda não tem conta? <a href="/register">Registre-se</a></p>
        </div>
    </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@video_app.post("/login")
def login_action(username: str = Form(...), password: str = Form(...)):
    username = username.strip().lower()
    user_data = get_user(username)

    if user_data and user_data["password"] == password:
        # Cria sessão
        session_id = create_session(username)
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(key=SESSION_COOKIE_NAME, value=session_id, httponly=True)
        return response

    # Caso contrário, credenciais inválidas
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Login Inválido</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

            body {
                font-family: 'Roboto', sans-serif;
                background: linear-gradient(135deg, #e74c3c, #c0392b);
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .message {
                background: rgba(255, 255, 255, 0.1);
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                backdrop-filter: blur(8.5px);
                -webkit-backdrop-filter: blur(8.5px);
                border: 1px solid rgba(255, 255, 255, 0.18);
                text-align: center;
                animation: fadeIn 1s ease-in-out;
            }
            h1 {
                color: #fff;
                margin-bottom: 20px;
            }
            a {
                color: #ecf0f1;
                text-decoration: none;
                font-weight: bold;
            }
            a:hover {
                color: #bdc3c7;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: scale(0.8); }
                to { opacity: 1; transform: scale(1); }
            }
        </style>
    </head>
    <body>
    <div class="message">
        <h1>Usuário ou senha inválidos!</h1>
        <a href='/login'>Tentar novamente</a>
    </div>
    </body>
    </html>
    """
    return HTMLResponse(html, status_code=401)

@video_app.get("/register", response_class=HTMLResponse)
def register_page():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Registro - Filmes do Boteco</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

            body {
                font-family: 'Roboto', sans-serif;
                background: linear-gradient(135deg, #f39c12, #d35400);
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .container {
                background: rgba(255, 255, 255, 0.1);
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                backdrop-filter: blur(8.5px);
                -webkit-backdrop-filter: blur(8.5px);
                border: 1px solid rgba(255, 255, 255, 0.18);
                width: 400px;
                animation: slideIn 1s ease-in-out;
            }
            h1 {
                text-align: center;
                color: #fff;
                margin-bottom: 30px;
            }
            label {
                display: block;
                margin-bottom: 5px;
                color: #fff;
            }
            input[type="text"],
            input[type="password"] {
                width: 100%;
                padding: 10px;
                margin-bottom: 20px;
                border: none;
                border-radius: 8px;
                outline: none;
                transition: box-shadow 0.3s;
            }
            input[type="text"]:focus,
            input[type="password"]:focus {
                box-shadow: 0 0 10px rgba(255, 255, 255, 0.7);
            }
            button {
                width: 100%;
                padding: 10px;
                border: none;
                border-radius: 8px;
                background-color: #e67e22;
                color: #fff;
                font-weight: bold;
                cursor: pointer;
                transition: background-color 0.3s;
            }
            button:hover {
                background-color: #d35400;
            }
            .info {
                background: rgba(255, 255, 255, 0.2);
                padding: 15px;
                border-radius: 8px;
                color: #fff;
                margin-bottom: 20px;
                animation: fadeIn 2s ease-in-out;
            }
            .links {
                text-align: center;
            }
            a {
                color: #ecf0f1;
                text-decoration: none;
                font-weight: bold;
            }
            a:hover {
                color: #bdc3c7;
            }
            @keyframes slideIn {
                from { opacity: 0; transform: translateX(100px); }
                to { opacity: 1; transform: translateX(0); }
            }
            @keyframes fadeIn {
                from { opacity: 0; }
                to { opacity: 1; }
            }
        </style>
    </head>
    <body>
    <div class="container">
        <h1>Registro</h1>
        <form method="post" action="/register">
            <label for="username">Usuário:</label>
            <input type="text" id="username" name="username" required>

            <label for="password">Senha:</label>
            <input type="password" id="password" name="password" required>

            <button type="submit">Criar Conta</button>
        </form>
        <div class="info">
            <p>Após criar sua conta, entre em nosso servidor Discord <strong><a href="https://discord.gg/daJ6hHHfG4" target="_blank">clicando aqui</a></strong> e aguarde a aprovação no canal interno.</p>
        </div>
        <div class="links">
            <p>Já tem conta? <a href="/login">Fazer login</a></p>
        </div>
    </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@video_app.post("/register")
def register_action(request: Request, username: str = Form(...), password: str = Form(...)):
    username = username.strip().lower()
    if not username or not password:
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Registro Inválido</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

                body {
                    font-family: 'Roboto', sans-serif;
                    background: linear-gradient(135deg, #f1c40f, #f39c12);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .message {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                    backdrop-filter: blur(8.5px);
                    -webkit-backdrop-filter: blur(8.5px);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    text-align: center;
                    animation: fadeIn 1s ease-in-out;
                }
                h1 {
                    color: #fff;
                    margin-bottom: 20px;
                }
                a {
                    color: #ecf0f1;
                    text-decoration: none;
                    font-weight: bold;
                }
                a:hover {
                    color: #bdc3c7;
                }
                @keyframes fadeIn {
                    from { opacity: 0; transform: scale(0.8); }
                    to { opacity: 1; transform: scale(1); }
                }
            </style>
        </head>
        <body>
        <div class="message">
            <h1>Dados inválidos.</h1>
            <a href='/register'>Voltar</a>
        </div>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=400)

    # Verifica se usuário já existe ou se for "eletriom"
    if get_user(username) is not None or username == "eletriom":
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Registro de Usuário</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

                body {
                    font-family: 'Roboto', sans-serif;
                    background: linear-gradient(135deg, #e67e22, #d35400);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .message {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                    backdrop-filter: blur(8.5px);
                    -webkit-backdrop-filter: blur(8.5px);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    text-align: center;
                    animation: fadeIn 1s ease-in-out;
                }
                h1 {
                    color: #fff;
                    margin-bottom: 20px;
                }
                a {
                    color: #ecf0f1;
                    text-decoration: none;
                    font-weight: bold;
                }
                a:hover {
                    color: #bdc3c7;
                }
                @keyframes fadeIn {
                    from { opacity: 0; transform: scale(0.8); }
                    to { opacity: 1; transform: scale(1); }
                }
            </style>
        </head>
        <body>
        <div class="message">
            <h1>Usuário já existe!</h1>
            <a href='/register'>Voltar</a>
        </div>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=400)

    # Cria usuário no DB com approved=False, admin=False
    create_user(username, password)

    # Dispara mensagem no canal de aprovação do Discord
    # Canal: 1250962809756454932
    submit_approval_request(username, bot, 1250962809756454932)

    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Registro Sucesso</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

            body {
                font-family: 'Roboto', sans-serif;
                background: linear-gradient(135deg, #16a085, #1abc9c);
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .message {
                background: rgba(255, 255, 255, 0.1);
                padding: 40px;
                border-radius: 15px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                backdrop-filter: blur(8.5px);
                -webkit-backdrop-filter: blur(8.5px);
                border: 1px solid rgba(255, 255, 255, 0.18);
                text-align: center;
                animation: fadeIn 1s ease-in-out;
            }
            h1 {
                color: #fff;
                margin-bottom: 20px;
            }
            p {
                color: #ecf0f1;
                margin-bottom: 20px;
            }
            a {
                display: inline-block;
                margin-top: 20px;
                padding: 10px 20px;
                background: #2980b9;
                color: #fff;
                border-radius: 8px;
                text-decoration: none;
                transition: background-color 0.3s;
            }
            a:hover {
                background: #3498db;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: scale(0.8); }
                to { opacity: 1; transform: scale(1); }
            }
        </style>
    </head>
    <body>
    <div class="message">
        <h1>Conta criada com sucesso!</h1>
        <p>Seu usuário aguarda aprovação do Admin. Assim que for aprovado, você poderá acessar.</p>
        <a href='/login'>Fazer Login</a>
    </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@video_app.get("/logout")
def logout_action(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if session_id in active_sessions:
        del active_sessions[session_id]
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response

#########################################
# ADMIN: DEFINIR OUTROS ADMINS
#########################################

@video_app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    """
    Página para somente definir outro usuário como admin.
    Somente admins podem acessar.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    current_user = get_current_username_from_session(session_id)
    if not current_user or not is_admin(current_user):
        # Acesso negado
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Acesso Negado</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

                body {
                    font-family: 'Roboto', sans-serif;
                    background: linear-gradient(135deg, #e74c3c, #c0392b);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }
                .message {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                    backdrop-filter: blur(8.5px);
                    -webkit-backdrop-filter: blur(8.5px);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    text-align: center;
                    animation: fadeIn 1s ease-in-out;
                }
                h1 {
                    color: #fff;
                    margin-bottom: 20px;
                }
                a {
                    color: #ecf0f1;
                    text-decoration: none;
                    font-weight: bold;
                }
                a:hover {
                    color: #bdc3c7;
                }
                @keyframes fadeIn {
                    from { opacity: 0; transform: scale(0.8); }
                    to { opacity: 1; transform: scale(1); }
                }
            </style>
        </head>
        <body>
        <div class="message">
            <h1>Acesso negado</h1>
            <a href='/login'>Login</a>
        </div>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=403)

    # Monta form para setar admin
    top_bar = f"""
    <div class="top-bar">
        <div class="title">Bem-vindo, {current_user} (Admin)!</div>
        <div class="menu">
            <a href="/logout" class="btn">Logout</a>
        </div>
    </div>
    """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Painel do Admin</title>
      <style>
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

        body {{
          font-family: 'Roboto', sans-serif;
          background: linear-gradient(135deg, #34495e, #2c3e50);
          color: #ecf0f1;
          margin: 0;
          padding: 0;
        }}
        .top-bar {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 60px;
            background: rgba(0, 0, 0, 0.7);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 20px;
            z-index: 1000;
            animation: fadeIn 1s ease-in-out;
            box-sizing: border-box;
        }}
        .top-bar .title {{
            font-size: 1.5rem;
            font-weight: bold;
            color: #1abc9c;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
        }}
        .top-bar .menu {{
            display: flex;
            gap: 10px;
        }}
        .top-bar .menu a {{
            margin-left: 15px;
            padding: 8px 16px;
            background: #2980b9;
            border-radius: 8px;
            text-decoration: none;
            color: #fff;
            transition: background 0.3s;
        }}
        .top-bar .menu a:hover {{
            background: #3498db;
        }}
        .container {{
          margin: 80px auto;
          max-width: 500px;
          background: rgba(255, 255, 255, 0.1);
          padding: 40px;
          border-radius: 15px;
          box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
          backdrop-filter: blur(8.5px);
          -webkit-backdrop-filter: blur(8.5px);
          border: 1px solid rgba(255, 255, 255, 0.18);
          text-align: center;
          animation: fadeIn 1s ease-in-out;
        }}
        h1 {{
          text-align: center;
          margin-bottom: 30px;
        }}
        label {{
          display: block;
          margin: 10px 0 5px;
        }}
        input[type="text"] {{
          width: 100%;
          padding: 10px;
          margin-bottom: 20px;
          border-radius: 8px;
          border: none;
          outline: none;
        }}
        button {{
          background: #27ae60;
          border: none;
          padding: 10px 20px;
          border-radius: 8px;
          cursor: pointer;
          color: #fff;
          transition: background-color 0.3s;
          font-weight: bold;
        }}
        button:hover {{
          background: #2ecc71;
        }}
        @keyframes fadeIn {{
            from {{ opacity: 0; transform: translateY(-50px); }}
            to {{ opacity: 1; transform: translateY(0); }}
        }}
      </style>
    </head>
    <body>
      {top_bar}
      <div class="container">
        <h1>Configurações de Admin</h1>
        <form method="post" action="/admin/set_admin">
            <label for="username">Definir usuário como admin:</label>
            <input type="text" id="username" name="username" required />
            <button type="submit">Tornar Admin</button>
        </form>
      </div>
    </body>
    </html>
    """
    return HTMLResponse(html)

@video_app.post("/admin/set_admin")
def admin_set_user_as_admin(request: Request, username: str = Form(...)):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    current_user = get_current_username_from_session(session_id)
    if not current_user or not is_admin(current_user):
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Acesso Negado</title>
        </head>
        <body>
            <h1>Acesso negado</h1>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=403)

    username = username.strip().lower()
    user_data = get_user(username)
    if not user_data:
        return HTMLResponse(f"<p>Usuário '{username}' não encontrado.</p><a href='/admin'>Voltar</a>", status_code=400)

    set_admin(username, True)
    return RedirectResponse(url="/admin", status_code=302)

#########################################
# VARIÁVEIS GLOBAIS PARA CONTROLE DE TRANSCODIFICAÇÃO
#########################################

transcoding_progress = {}
transcoding_tasks = {}

#########################################
# FUNÇÕES AUXILIARES
#########################################

def format_title(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    return base.replace("_", " ")

def get_cover_image(filename: str) -> str:
    base = os.path.splitext(filename)[0]
    possible_exts = [".jpg", ".jpeg", ".png", ".webp"]
    for ext in possible_exts:
        candidate = os.path.join(IMAGENS_FOLDER, base + ext)
        if os.path.isfile(candidate):
            return f"/imagens/{os.path.basename(candidate)}"
    return "/imagens/no_image.jpg"

def get_subtitle_path(filename: str) -> str | None:
    base = os.path.splitext(filename)[0]
    possible_exts = [".vtt", ".srt"]
    for ext in possible_exts:
        candidate = os.path.join(LEGENDAS_FOLDER, base + ext)
        if os.path.isfile(candidate):
            return candidate
    return None

async def get_video_duration_s(file_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        file_path
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        info = json.loads(stdout.decode())
        fmt = info.get("format", {})
        dur_str = fmt.get("duration", "0")
        return float(dur_str)
    except:
        return 0.0

async def transcode_file(original_file: str, transcoded_path: str):
    filename = os.path.basename(original_file)
    duration_s = await get_video_duration_s(original_file)
    if duration_s <= 0:
        duration_s = 1

    transcoding_progress[filename] = {
        "percent": 0.0,
        "eta": 0.0,
        "status": "in_progress",
        "start_time": time.time(),
        "duration_s": duration_s,
    }

    cmd = [
        "ffmpeg",
        "-y",
        "-i", original_file,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "faststart",
        "-progress", "pipe:2",
        "-nostats",
        transcoded_path
    ]

    print(f"[Transcode] Iniciando: {original_file} -> {transcoded_path}")
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    while True:
        line = await process.stderr.readline()
        if not line:
            break
        decoded = line.decode().strip()
        if "out_time_ms=" in decoded:
            val_str = decoded.split("=")[1].strip()
            try:
                out_time_ms = float(val_str)
                out_time_s = out_time_ms / 1_000_000.0
                pct = (out_time_s / duration_s) * 100
                pct = max(0, min(pct, 100))
                elapsed = time.time() - transcoding_progress[filename]["start_time"]
                speed = out_time_s / elapsed if elapsed > 0 else 0
                if speed > 0:
                    remaining_s = (duration_s - out_time_s) / speed
                else:
                    remaining_s = 0

                transcoding_progress[filename]["percent"] = pct
                transcoding_progress[filename]["eta"] = remaining_s
            except:
                pass

    await process.wait()
    if process.returncode != 0:
        print(f"[Transcode] Erro ao transcodificar {filename}")
        transcoding_progress[filename]["status"] = "error"
        return

    transcoding_progress[filename]["percent"] = 100.0
    transcoding_progress[filename]["eta"] = 0.0
    transcoding_progress[filename]["status"] = "done"

    try:
        os.remove(original_file)
        print(f"[Transcode] Arquivo original removido: {original_file}")
    except Exception as e:
        print(f"[Transcode] Erro ao remover {original_file}: {e}")

    print(f"[Transcode] Concluído: {transcoded_path}")

async def ensure_transcoded(original_file: str) -> str:
    filename = os.path.basename(original_file)
    transcoded_path = os.path.join(TRANSCODED_FOLDER, filename + ".mp4")

    if filename in transcoding_tasks:
        ttask = transcoding_tasks[filename]
        if not ttask.done():
            await ttask
            return transcoded_path

    prog = transcoding_progress.get(filename, {})
    if prog.get("status") == "done" and os.path.isfile(transcoded_path):
        return transcoded_path

    task = asyncio.create_task(transcode_file(original_file, transcoded_path))
    transcoding_tasks[filename] = task
    await task
    return transcoded_path

#########################################
# ENDPOINTS FASTAPI
#########################################

@video_app.get("/ads.txt", response_class=PlainTextResponse)
def ads_txt():
    return "google.com, pub-6682849033272336, DIRECT, f08c47fec0942fa0"

@video_app.get("/list")
def list_filmes():
    supported_exts = (".mp4", ".mkv", ".avi", ".mov", ".flv")
    orig_files = []
    if os.path.exists(VIDEO_FOLDER):
        for f in os.listdir(VIDEO_FOLDER):
            if f.lower().endswith(supported_exts):
                orig_files.append(f)
    transcoded_files = []
    if os.path.exists(TRANSCODED_FOLDER):
        for f in os.listdir(TRANSCODED_FOLDER):
            if f.lower().endswith(".mp4"):
                base_no_mp4, _ = os.path.splitext(f)
                transcoded_files.append(base_no_mp4)

    all_files = set(orig_files + transcoded_files)
    return {"filmes": sorted(all_files)}

@video_app.get("/download")
def download_video(filename: str):
    transcoded_path = os.path.join(TRANSCODED_FOLDER, filename + ".mp4")
    if os.path.isfile(transcoded_path):
        return FileResponse(transcoded_path, media_type='application/octet-stream', filename=filename)
    original_path = os.path.join(VIDEO_FOLDER, filename)
    if not os.path.isfile(original_path):
        raise HTTPException(status_code=404, detail="Filme não encontrado")
    return FileResponse(original_path, media_type='application/octet-stream', filename=filename)

@video_app.get("/", response_class=HTMLResponse)
def read_root(request: Request):
    """
    Página inicial. Somente exibe a lista de filmes se o usuário estiver logado e aprovado.
    Caso contrário, exibe mensagem ou redireciona para login.
    """
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    username = get_current_username_from_session(session_id)

    if not username:
        # Usuário não logado
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Bem-vindo - Filmes do Boteco</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
                @keyframes fadeIn {
                    from { opacity: 0; transform: translateY(-20px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                body {
                    font-family: 'Roboto', sans-serif;
                    background: linear-gradient(135deg, #1abc9c, #16a085);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    color: #fff;
                }
                .message {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                    backdrop-filter: blur(8.5px);
                    -webkit-backdrop-filter: blur(8.5px);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    text-align: center;
                    animation: fadeIn 1s ease-in-out;
                }
                h1 {
                    margin-bottom: 20px;
                }
                a {
                    display: inline-block;
                    margin-top: 20px;
                    padding: 10px 20px;
                    background: #2980b9;
                    color: #fff;
                    border-radius: 8px;
                    text-decoration: none;
                    transition: background-color 0.3s;
                }
                a:hover {
                    background: #3498db;
                }
                footer {
                    position: fixed;
                    bottom: 10px;
                    left: 0;
                    width: 100%;
                    text-align: center;
                    color: #ecf0f1;
                    font-size: 0.9rem;
                }
            </style>
        </head>
        <body>
        <div class="message">
            <h1>Bem-vindo ao Filmes do Boteco!</h1>
            <p>Este serviço é exclusivo para membros aprovados do nosso servidor Discord.</p>
            <a href="/login">Fazer Login</a>
        </div>
        <footer>
            <p>© 2025 - By Eletriom</p>
        </footer>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=200)

    if not is_approved_user(username):
        # Usuário logado mas não aprovado
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Aguarde Aprovação - Filmes do Boteco</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
                @keyframes fadeIn {
                    from { opacity: 0; transform: translateY(-20px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                body {
                    font-family: 'Roboto', sans-serif;
                    background: linear-gradient(135deg, #8e44ad, #9b59b6);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    color: #fff;
                }
                .message {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                    backdrop-filter: blur(8.5px);
                    -webkit-backdrop-filter: blur(8.5px);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    text-align: center;
                    animation: fadeIn 1s ease-in-out;
                }
                h1 {
                    margin-bottom: 20px;
                }
                a {
                    display: inline-block;
                    margin-top: 20px;
                    padding: 10px 20px;
                    background: #2980b9;
                    color: #fff;
                    border-radius: 8px;
                    text-decoration: none;
                    transition: background-color 0.3s;
                }
                a:hover {
                    background: #3498db;
                }
                footer {
                    position: fixed;
                    bottom: 10px;
                    left: 0;
                    width: 100%;
                    text-align: center;
                    color: #ecf0f1;
                    font-size: 0.9rem;
                }
            </style>
        </head>
        <body>
        <div class="message">
            <h1>Aguarde Aprovação!</h1>
            <p>Você já está registrado, mas ainda não foi aprovado pelo Admin.</p>
            <p>Entre em contato via Discord para aprovação.</p>
            <a href="/logout">Sair</a>
        </div>
        <footer>
            <p>© 2025 - By Eletriom</p>
        </footer>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=200)

    # Se chegou aqui, está logado e aprovado
    server_url = "http://eletriom.com.br:25614"
    data = list_filmes()
    filmes = data.get("filmes", [])

    # Top bar
    top_bar = f"""
    <div class="top-bar">
        <div class="title">Bem-vindo, {username}!</div>
        <div class="menu">
            <a href="/logout" class="btn">Logout</a>
            {"<a href='/admin' class='btn'>Admin</a>" if is_admin(username) else ""}
        </div>
    </div>
    """

    html_header = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Filmes do Boteco</title>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
            @keyframes fadeIn {{
                from {{ opacity: 0; }}
                to {{ opacity: 1; }}
            }}
            body {{
                font-family: 'Roboto', sans-serif;
                margin: 0;
                padding: 0;
                background: linear-gradient(135deg, #1abc9c, #16a085);
                color: #fff;
            }}
            .top-bar {{
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 60px;
                background: rgba(0, 0, 0, 0.7);
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 0 20px;
                z-index: 1000;
                animation: fadeIn 1s ease-in-out;
            }}
            .top-bar .title {{
                font-size: 1.5rem;
                font-weight: bold;
                color: #1abc9c;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            }}
            .top-bar .menu {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
            }}
            .top-bar .menu a {{
                margin-left: 15px;
                padding: 8px 16px;
                background: #2980b9;
                border-radius: 8px;
                text-decoration: none;
                color: #fff;
                transition: background 0.3s;
            }}
            .top-bar .menu a:hover {{
                background: #3498db;
            }}
            .content {{
                padding-top: 80px;
                padding-bottom: 60px;
                animation: fadeIn 2s ease-in-out;
            }}
            .container {{
                display: flex;
                flex-wrap: wrap;
                justify-content: center;
                gap: 20px;
                padding: 20px;
            }}
            .card {{
                background: rgba(255, 255, 255, 0.1);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 15px;
                width: 200px;
                overflow: hidden;
                text-align: center;
                transition: transform 0.3s, box-shadow 0.3s;
            }}
            .card:hover {{
                transform: scale(1.05);
                box-shadow: 0 8px 16px rgba(0,0,0,0.3);
            }}
            .card img {{
                width: 100%;
                height: 300px;
                object-fit: cover;
                border-bottom: 1px solid rgba(255, 255, 255, 0.2);
            }}
            .card-title {{
                padding: 15px 10px;
                font-size: 1.1rem;
                font-weight: bold;
            }}
            .card a {{
                display: inline-block;
                margin: 10px 0;
                padding: 10px 20px;
                background: #e67e22;
                color: #fff;
                border-radius: 8px;
                text-decoration: none;
                transition: background 0.3s;
            }}
            .card a:hover {{
                background: #d35400;
            }}
            .presentation {{
                max-width: 800px;
                margin: 20px auto;
                text-align: center;
                padding: 20px;
                background: rgba(255, 255, 255, 0.1);
                border-radius: 15px;
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                backdrop-filter: blur(8.5px);
                -webkit-backdrop-filter: blur(8.5px);
                border: 1px solid rgba(255, 255, 255, 0.18);
                animation: fadeIn 1.5s ease-in-out;
            }}
            .presentation h2 {{
                margin-bottom: 15px;
            }}
            .presentation p {{
                line-height: 1.6;
            }}
            .discord-link img {{
                height: 40px;
                transition: transform 0.3s;
            }}
            .discord-link img:hover {{
                transform: scale(1.1);
            }}
            .nerd-container {{
                text-align: center;
                padding: 20px;
            }}
            .nerd-container img {{
                max-width: 400px;
                width: 100%;
                border: 2px solid #1abc9c;
                border-radius: 10px;
                animation: fadeIn 2s ease-in-out;
            }}
            footer {{
                position: fixed;
                bottom: 0;
                left: 0;
                width: 100%;
                background: rgba(0, 0, 0, 0.7);
                text-align: center;
                padding: 15px 0;
                color: #ecf0f1;
                font-size: 0.9rem;
                animation: fadeIn 1s ease-in-out;
            }}
            @media (max-width: 600px) {{
                .top-bar {{
                    flex-direction: column;
                    align-items: flex-start;
                    padding: 10px;
                    height: auto;
                }}
                .top-bar .title {{
                    margin-bottom: 10px;
                }}
                .top-bar .menu a {{
                    width: 100%;
                    text-align: center;
                }}
                .card {{
                    width: 100%;
                }}
                .presentation {{
                    margin: 10px;
                    padding: 15px;
                }}
            }}
        </style>
    </head>
    <body>
    {top_bar}
    <div class="content">
        <div class="presentation">
            <h2>Bem-vindo ao Filmes do Boteco!</h2>
            <p>Este serviço é exclusivo para membros aprovados do nosso servidor Discord. Aqui você pode assistir aos melhores filmes selecionados, todos disponíveis em alta qualidade. Navegue pela nossa lista de filmes e aproveite!</p>
            <div class="discord-link">
                <a href="https://discord.gg/daJ6hHHfG4" target="_blank">
                    <img src="/imagens/discord.png" alt="Discord" />
                </a>
            </div>
        </div>
        <div class="container">
    """

    html_body = ""
    if not filmes:
        html_body += "<p style='color: #ecf0f1;'>Nenhum filme disponível no momento.</p>"
    else:
        for v in filmes:
            titulo = format_title(v)
            cover_url = get_cover_image(v)
            link = f"{server_url}/filmes?filename={v}"
            html_body += f"""
            <div class="card">
                <img src="{cover_url}" alt="{titulo}" />
                <div class="card-title">{titulo}</div>
                <a href="{link}">Assistir</a>
            </div>
            """

    html_footer = """
        </div> <!-- .container -->
        <div class="nerd-container">
            <img src="/imagens/nerd.jpg" alt="Nerd" />
        </div>
    </div> <!-- .content -->
    <footer>
        <p>© 2025 - By Eletriom</p>
    </footer>
    </body>
    </html>
    """

    return HTMLResponse(html_header + html_body + html_footer)

@video_app.get("/filmes", response_class=HTMLResponse)
async def plyr_player(request: Request, filename: str):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    username = get_current_username_from_session(session_id)
    if not username or not is_approved_user(username):
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Acesso Negado</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
                @keyframes fadeIn {
                    from { opacity: 0; transform: translateY(-20px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                body {
                    font-family: 'Roboto', sans-serif;
                    background: linear-gradient(135deg, #e74c3c, #c0392b);
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                    color: #fff;
                }
                .message {
                    background: rgba(255, 255, 255, 0.1);
                    padding: 40px;
                    border-radius: 15px;
                    box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                    backdrop-filter: blur(8.5px);
                    -webkit-backdrop-filter: blur(8.5px);
                    border: 1px solid rgba(255, 255, 255, 0.18);
                    text-align: center;
                    animation: fadeIn 1s ease-in-out;
                }
                h1 {
                    margin-bottom: 20px;
                }
                a {
                    display: inline-block;
                    margin-top: 20px;
                    padding: 10px 20px;
                    background: #2980b9;
                    color: #fff;
                    border-radius: 8px;
                    text-decoration: none;
                    transition: background-color 0.3s;
                }
                a:hover {
                    background: #3498db;
                }
                footer {
                    position: fixed;
                    bottom: 10px;
                    left: 0;
                    width: 100%;
                    text-align: center;
                    color: #ecf0f1;
                    font-size: 0.9rem;
                }
            </style>
        </head>
        <body>
        <div class="message">
            <h1>Acesso negado.</h1>
            <p>Faça login e aguarde aprovação para assistir.</p>
            <a href="/login">Login</a>
        </div>
        <footer>
            <p>© 2025 - By Eletriom</p>
        </footer>
        </body>
        </html>
        """
        return HTMLResponse(html, status_code=403)

    server_url = "http://eletriom.com.br:25614"
    download_url = f"{server_url}/download?filename={filename}"
    nome_formatado = format_title(filename)

    original_path = os.path.join(VIDEO_FOLDER, filename)
    transcoded_path = os.path.join(TRANSCODED_FOLDER, filename + ".mp4")

    progress_info = transcoding_progress.get(filename)

    if progress_info and progress_info["status"] == "in_progress":
        return HTMLResponse(content=progress_page_html(nome_formatado, filename), status_code=200)

    if progress_info and progress_info["status"] == "done" and os.path.isfile(transcoded_path):
        return HTMLResponse(content=player_page_html(nome_formatado, filename, server_url, download_url), status_code=200)

    if os.path.isfile(transcoded_path):
        return HTMLResponse(content=player_page_html(nome_formatado, filename, server_url, download_url), status_code=200)

    if os.path.isfile(original_path):
        transcoding_progress[filename] = {
            "percent": 0.0,
            "eta": 0.0,
            "status": "in_progress",
            "start_time": time.time(),
            "duration_s": 0.0,
        }
        if filename not in transcoding_tasks or transcoding_tasks[filename].done():
            task = asyncio.create_task(transcode_file(original_path, transcoded_path))
            transcoding_tasks[filename] = task

        return HTMLResponse(content=progress_page_html(nome_formatado, filename), status_code=200)

    raise HTTPException(status_code=404, detail="Filme não encontrado")

def player_page_html(nome_formatado: str, filename: str, server_url: str, download_url: str) -> str:
    subtitle_path = get_subtitle_path(filename)
    if subtitle_path is not None:
        subtitle_file = os.path.basename(subtitle_path)
        subtitles_url = f"{server_url}/legendas/{subtitle_file}"
        track_tag = f'<track kind="subtitles" label="Português" src="{subtitles_url}" srclang="pt" default />'
    else:
        track_tag = ""

    return f"""
    <!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8" />
        <title>{nome_formatado} - Filmes do Boteco</title>
        <link rel="stylesheet" href="https://cdn.plyr.io/3.7.8/plyr.css" />
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');

            body {{
                font-family: 'Roboto', sans-serif;
                margin: 0;
                background: linear-gradient(135deg, #1abc9c, #16a085);
                color: #fff;
            }}
            .top-bar {{
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 60px;
                background: rgba(0, 0, 0, 0.7);
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 0 20px;
                z-index: 1000;
                animation: fadeIn 1s ease-in-out;
            }}
            .top-bar .title {{
                font-size: 1.5rem;
                font-weight: bold;
                color: #1abc9c;
                text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            }}
            .top-bar .discord-link img {{
                height: 40px;
                transition: transform 0.3s;
            }}
            .top-bar .discord-link img:hover {{
                transform: scale(1.1);
            }}
            .top-bar .menu {{
                display: flex;
                align-items: center;
            }}
            @keyframes fadeIn {{
                from {{ opacity: 0; }}
                to {{ opacity: 1; }}
            }}
            .content {{
                padding-top: 80px;
                max-width: 1000px;
                margin: 0 auto;
                animation: fadeIn 1.5s ease-in-out;
            }}
            h1 {{
                margin-bottom: 20px;
                font-size: 2rem;
                text-align: center;
                text-transform: uppercase;
                letter-spacing: 1px;
                text-shadow: 2px 2px 6px rgba(0,0,0,0.6);
            }}
            .buttons-container {{
                display: flex;
                gap: 20px;
                justify-content: center;
                margin-bottom: 30px;
            }}
            .btn {{
                background: #e67e22;
                color: #fff;
                border: none;
                padding: 12px 24px;
                border-radius: 8px;
                font-size: 1rem;
                font-weight: bold;
                cursor: pointer;
                transition: background 0.3s;
                text-decoration: none;
                display: inline-block;
            }}
            .btn:hover {{
                background: #d35400;
            }}
            #player-container {{
                width: 100%;
                max-width: 900px;
                margin: 0 auto;
                position: relative;
                border: 2px solid #1abc9c;
                border-radius: 15px;
                box-shadow: 0 0 20px rgba(0,0,0,0.5);
                animation: fadeIn 2s ease-in-out;
            }}
            video {{
                width: 100%;
                height: auto;
                border-radius: 15px;
            }}
            footer {{
                text-align: center;
                color: #ecf0f1;
                padding: 20px;
                font-size: 0.9rem;
                background: rgba(0, 0, 0, 0.7);
                position: fixed;
                bottom: 0;
                left: 0;
                width: 100%;
                animation: fadeIn 1s ease-in-out;
            }}
        </style>
      </head>
      <body>
        <div class="top-bar">
          <div class="title">Filmes do Boteco</div>
          <div class="discord-link">
            <a href="https://discord.gg/daJ6hHHfG4" target="_blank">
              <img src="/imagens/discord.png" alt="Discord" />
            </a>
          </div>
        </div>
        <div class="content">
          <h1>{nome_formatado}</h1>
          <div class="buttons-container">
            <a href="{server_url}" class="btn">Início</a>
            <a href="{download_url}" class="btn" target="_blank">Baixar</a>
          </div>
          <div id="player-container">
            <video id="player" playsinline controls>
              <source src="{server_url}/video?filename={filename}" type="video/mp4" />
              {track_tag}
            </video>
          </div>
          <footer>
            <p>© 2025 - By Eletriom</p>
          </footer>
        </div>

        <script src="https://cdn.plyr.io/3.7.8/plyr.js"></script>
        <script>
          const player = new Plyr('#player', {{
            fullscreen: {{
              enabled: true,
              fallback: true,
              iosNative: false
            }}
          }});
        </script>
      </body>
    </html>
    """

def progress_page_html(nome_formatado: str, filename: str) -> str:
    server_url = "http://eletriom.com.br:25614"
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8" />
      <title>Transcodificando {nome_formatado}...</title>
      <style>
        @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap');
        @keyframes fadeIn {{
            from {{ opacity: 0; }}
            to {{ opacity: 1; }}
        }}
        body {{
            font-family: 'Roboto', sans-serif;
            margin: 0;
            background: linear-gradient(135deg, #f1c40f, #f39c12);
            color: #fff;
            height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }}
        .top-bar {{
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 60px;
            background: rgba(0, 0, 0, 0.7);
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 20px;
            z-index: 1000;
            animation: fadeIn 1s ease-in-out;
        }}
        .top-bar .title {{
            font-size: 1.5rem;
            font-weight: bold;
            color: #f39c12;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
        }}
        .top-bar .discord-link img {{
            height: 40px;
            transition: transform 0.3s;
        }}
        .top-bar .discord-link img:hover {{
            transform: scale(1.1);
        }}
        .top-bar .menu {{
            display: flex;
            align-items: center;
        }}
        .content {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            animation: fadeIn 1.5s ease-in-out;
        }}
        .progress-container {{
            background: rgba(255, 255, 255, 0.1);
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
            backdrop-filter: blur(8.5px);
            -webkit-backdrop-filter: blur(8.5px);
            border: 1px solid rgba(255, 255, 255, 0.18);
            text-align: center;
            width: 80%;
            max-width: 500px;
        }}
        h1 {{
            margin-bottom: 20px;
            color: #fff;
        }}
        .bar {{
            width: 100%;
            background: #ddd;
            border-radius: 20px;
            overflow: hidden;
            margin-bottom: 20px;
            height: 30px;
            position: relative;
        }}
        .bar-fill {{
            height: 100%;
            background: linear-gradient(90deg, #1abc9c, #16a085);
            width: 0%;
            transition: width 0.5s ease;
        }}
        .bar-text {{
            position: absolute;
            top: 0;
            left: 50%;
            transform: translateX(-50%);
            height: 30px;
            line-height: 30px;
            color: #fff;
            font-weight: bold;
        }}
        .eta {{
            margin-top: 10px;
            font-size: 1rem;
        }}
        .btn-home {{
            display: inline-block;
            margin-top: 20px;
            padding: 10px 20px;
            background: #e67e22;
            color: #fff;
            border: none;
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
            transition: background 0.3s;
        }}
        .btn-home:hover {{
            background: #d35400;
        }}
        footer {{
            position: fixed;
            bottom: 10px;
            left: 0;
            width: 100%;
            text-align: center;
            color: #ecf0f1;
            font-size: 0.9rem;
            animation: fadeIn 1s ease-in-out;
        }}
      </style>
    </head>
    <body>
      <div class="top-bar">
        <div class="title">Filmes do Boteco</div>
        <div class="discord-link">
          <a href="https://discord.gg/daJ6hHHfG4" target="_blank">
            <img src="/imagens/discord.png" alt="Discord" />
          </a>
        </div>
      </div>
      <div class="content">
        <div class="progress-container">
          <h1>Transcodificando <em>{nome_formatado}</em>...</h1>
          <div class="bar">
            <div class="bar-fill" id="bar-fill"></div>
            <div class="bar-text" id="bar-text">0%</div>
          </div>
          <div class="eta" id="eta-info">Aguarde...</div>
          <a href="{server_url}" class="btn-home">Voltar para Início</a>
        </div>
      </div>
      <footer>
        <p>© 2025 - By Eletriom</p>
      </footer>
      <script>
        const filename = "{filename}";
        const serverUrl = "{server_url}";

        async function checkProgress() {{
          try {{
            const resp = await fetch(serverUrl + "/progress?filename=" + filename);
            if (!resp.ok) return;
            const data = await resp.json();

            const barFill = document.getElementById('bar-fill');
            const barText = document.getElementById('bar-text');
            const etaDiv = document.getElementById('eta-info');

            let pct = data.percent || 0;
            if (pct < 0) pct = 0;
            if (pct > 100) pct = 100;

            barFill.style.width = pct.toFixed(1) + "%";
            barText.innerText = pct.toFixed(1) + "%";

            if (data.status === "done" || pct >= 100) {{
              barFill.style.width = "100%";
              barText.innerText = "100%";
              etaDiv.innerHTML = "Transcodificação concluída! Carregando vídeo...";
              setTimeout(() => {{
                window.location.reload();
              }}, 1500);
              return;
            }}

            if (data.eta > 0) {{
              let minutos = Math.floor(data.eta / 60);
              let segundos = Math.floor(data.eta % 60);
              etaDiv.innerHTML = "ETA: " + minutos + "min " + segundos + "s";
            }} else {{
              etaDiv.innerHTML = "Aguarde...";
            }}
          }} catch(e) {{
            console.log(e);
          }}
        }}

        setInterval(checkProgress, 1000);
        checkProgress();
      </script>
    </body>
    </html>
    """

@video_app.get("/progress")
def get_transcode_progress(filename: str):
    if filename not in transcoding_progress:
        return {"percent": 0.0, "eta": 0.0, "status": "not_found"}
    info = transcoding_progress[filename]
    return {
        "percent": info["percent"],
        "eta": info["eta"],
        "status": info["status"]
    }

@video_app.get("/video")
async def stream_video(request: Request, filename: str):
    transcoded_path = os.path.join(TRANSCODED_FOLDER, filename + ".mp4")
    if os.path.isfile(transcoded_path):
        final_path = transcoded_path
    else:
        original_path = os.path.join(VIDEO_FOLDER, filename)
        if not os.path.isfile(original_path):
            raise HTTPException(status_code=404, detail="Filme não encontrado")
        final_path = await ensure_transcoded(original_path)

    if not os.path.isfile(final_path):
        raise HTTPException(status_code=404, detail="Falha na transcodificação")

    file_size = os.path.getsize(final_path)
    range_header = request.headers.get("range", None)

    if range_header is None:
        def iterfile():
            with open(final_path, "rb") as f:
                yield from f
        return StreamingResponse(iterfile(), media_type="video/mp4")

    range_value = range_header.strip().split("=")[-1]
    try:
        start_str, end_str = range_value.split("-")
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
    except ValueError:
        start = 0
        end = file_size - 1

    if start >= file_size:
        return StreamingResponse(content=None, status_code=416)

    end = min(end, file_size - 1)
    content_length = (end - start) + 1

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length)
    }

    def iterfile():
        with open(final_path, "rb") as f:
            f.seek(start)
            remaining = content_length
            chunk_size = 1024 * 512
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    return StreamingResponse(
        iterfile(),
        media_type="video/mp4",
        status_code=206,
        headers=headers
    )

#########################################
# INICIALIZAÇÃO COM UVICORN
#########################################

def start_video_server():
    uvicorn.run(video_app, host="0.0.0.0", port=25614)

#########################################
# BOT DISCORD
#########################################

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# Canal onde postamos a "lista" (ou link principal):
CHANNEL_ID = 1241148816279998484

# Canal para aprovações (usado na função 'submit_approval_request'):
APPROVAL_CHANNEL_ID = 1250962809756454932

message_id = None

@bot.event
async def on_ready():
    print(f"Streaming Bot conectado como {bot.user}")
    # Inicia o servidor FastAPI em paralelo
    threading.Thread(target=start_video_server, daemon=True).start()
    # Inicia loop de checagem
    update_filmes_loop.start()

@tasks.loop(minutes=1)
async def update_filmes_loop():
    """
    A cada 1 minuto, atualiza a mensagem no canal (CHANNEL_ID)
    com o link da página inicial do servidor.
    """
    global message_id

    server_url = "http://eletriom.com.br:25614"
    mensagem = f"**Acesse a página inicial para assistir aos filmes:**\n{server_url}"

    try:
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            print(f"Canal {CHANNEL_ID} não encontrado.")
            return

        if message_id is None:
            msg = await channel.send(mensagem)
            message_id = msg.id
        else:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.edit(content=mensagem)
            except discord.NotFound:
                msg = await channel.send(mensagem)
                message_id = msg.id
    except Exception as e:
        print(f"Erro ao atualizar mensagem no canal: {e}")

DISCORD_TOKEN = os.getenv('DISCORDTOKEN2')
if DISCORD_TOKEN is None:
    raise ValueError("A variável de ambiente 'DISCORDTOKEN' não está definida.")

bot.run(DISCORD_TOKEN)
