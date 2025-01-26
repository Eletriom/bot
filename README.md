Funcionalidades Principais
Gerenciamento de Usuários:

Os usuários podem se registrar via interface web.
As contas aguardam aprovação de administradores, que recebem solicitações via canal no Discord.
A autenticação utiliza cookies de sessão e os dados são armazenados em um banco de dados SQLite.
Biblioteca de Vídeos:

Plataforma para listar e acessar filmes.
Suporte a múltiplos formatos de vídeo, com transcodificação automática para MP4 utilizando o FFmpeg.
Legendas são automaticamente vinculadas aos vídeos, caso estejam disponíveis.
Integração com Discord:

O bot do Discord gerencia a aprovação de novos usuários através de botões interativos (Aprovar/Negar).
Publica mensagens automáticas em canais específicos com links para a plataforma.
Transcodificação de Vídeos:

Converte vídeos para um formato otimizado para streaming.
Acompanha o progresso da transcodificação em tempo real, exibindo informações no frontend.
Interface de Usuário (Frontend):

Login, registro e páginas personalizadas para interação com a plataforma.
Design responsivo com animações e gradientes modernos.
Servidor FastAPI:

APIs para autenticação, listagem de vídeos, download e streaming.
Servidor rodando com suporte a CORS e rotas para arquivos estáticos, como imagens e legendas.
Estrutura do Projeto
Pasta /home/container:

/db: Banco de dados SQLite para gerenciar usuários.
/filmes: Armazena os vídeos originais.
/transcoded: Salva os vídeos transcodificados para MP4.
/imagens: Capa dos filmes e outros assets visuais.
/legendas: Armazena os arquivos de legendas.
FastAPI:

Gerencia o backend do site, incluindo autenticação, transcodificação e streaming de vídeos.
Discord Bot:

Aprovação de usuários diretamente no Discord.
Atualizações automáticas de links no canal principal do servidor.
Requisitos
Dependências:

Python 3.9+
FastAPI, Discord.py, Uvicorn, SQLite, FFmpeg
Outros pacotes especificados no requirements.txt
Variáveis de Ambiente:

DISCORDTOKEN2: Token do bot do Discord.
Como Iniciar
Configuração:

Certifique-se de que as dependências estão instaladas.
Configure as variáveis de ambiente, incluindo o token do bot Discord.
Execução:

Execute o script Python. O servidor FastAPI será iniciado automaticamente junto com o bot do Discord.
Acessar a Plataforma:

O servidor estará disponível no endereço configurado (por padrão, http://localhost:25614).
Licença
Este projeto foi desenvolvido por Eletriom e está disponível sob uma licença de uso pessoal e comunitário.
