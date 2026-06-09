"""Wizard internationalization: a pure-data message catalog and translator.

No Textual dependency — both the screens and the pure validators in ``state.py``
pull their user-facing strings from here, so the entire setup UI can render in
English or Brazilian Portuguese. The chosen language is a wizard-session choice
(picked on the first screen); it is NOT written to config.toml.

English ("en") is the source language and the fallback: any key missing a
translation falls back to its English text, and an unknown key returns itself.
Templates use ``str.format`` fields (``{name}``), so callers pass values as
keyword arguments to ``translate``.
"""

from __future__ import annotations

# Language code -> display name. The names are shown verbatim in the picker and
# are intentionally NOT translated (a speaker recognizes their own language name).
LANGUAGES: dict[str, str] = {
    "en": "English",
    "pt-BR": "Português-BR",
}
DEFAULT_LANGUAGE = "en"

# key -> {lang_code: template}. Every entry MUST have an "en" template (the
# fallback). Keys are grouped by screen for readability.
_CATALOG: dict[str, dict[str, str]] = {
    # --- navigation / shared (BaseStep) -------------------------------------
    "nav.back": {"en": "Back", "pt-BR": "Voltar"},
    "nav.next": {"en": "Next", "pt-BR": "Avançar"},
    "nav.cancel": {"en": "Cancel", "pt-BR": "Cancelar"},
    "nav.begin": {"en": "Begin", "pt-BR": "Começar"},
    "nav.finish": {"en": "Finish", "pt-BR": "Concluir"},
    # --- language picker (first screen) -------------------------------------
    # Bilingual on purpose: this screen renders before a language is chosen.
    "language.title": {"en": "Language · Idioma", "pt-BR": "Language · Idioma"},
    "language.help": {
        "en": "Select the wizard language · Selecione o idioma do assistente",
        "pt-BR": "Select the wizard language · Selecione o idioma do assistente",
    },
    # --- welcome ------------------------------------------------------------
    "welcome.title": {
        "en": "🐙 Welcome to OctoOps setup",
        "pt-BR": "🐙 Bem-vindo à configuração do OctoOps",
    },
    "welcome.intro": {
        "en": (
            "This wizard writes config.toml.\n\n"
            "You'll set up Telegram (control plane), the WhatsApp bridge "
            "(output), core settings, and which modules are enabled."
        ),
        "pt-BR": (
            "Este assistente cria o config.toml.\n\n"
            "Você vai configurar o Telegram (plano de controle), a ponte do "
            "WhatsApp (saída), as configurações principais e quais módulos "
            "ficam ativos."
        ),
    },
    "welcome.existing": {
        "en": (
            "\n⚠ An existing config.toml was found. Its current values are "
            "pre-filled below — review and adjust as needed. Finishing "
            "overwrites the file (a timestamped .bak backup is saved first)."
        ),
        "pt-BR": (
            "\n⚠ Um config.toml existente foi encontrado. Os valores atuais "
            "estão pré-preenchidos abaixo — revise e ajuste conforme necessário. "
            "Ao concluir, o arquivo é sobrescrito (um backup .bak com data e hora "
            "é salvo antes)."
        ),
    },
    # --- telegram -----------------------------------------------------------
    "telegram.title": {
        "en": "Telegram (control plane)",
        "pt-BR": "Telegram (plano de controle)",
    },
    "telegram.token_label": {
        "en": "Bot token  (get one from @BotFather → /newbot)",
        "pt-BR": "Token do bot  (obtenha um com o @BotFather → /newbot)",
    },
    "telegram.botfather_hint": {
        "en": (
            "Create your bot and copy its token at https://telegram.me/BotFather"
            " — open the chat and send /newbot."
        ),
        "pt-BR": (
            "Crie seu bot e copie o token em https://telegram.me/BotFather"
            " — abra o chat e envie /newbot."
        ),
    },
    "telegram.verify_button": {
        "en": "Verify token & auto-detect chat ID",
        "pt-BR": "Verificar token e detectar o chat ID",
    },
    "telegram.admin_label": {
        "en": "Admin chat ID (receives startup / error notices)",
        "pt-BR": "Chat ID do administrador (recebe avisos de início / erros)",
    },
    "telegram.pair.token_warn": {
        "en": "⚠ Bot token: {err}",
        "pt-BR": "⚠ Token do bot: {err}",
    },
    "telegram.pair.checking": {
        "en": "Checking token with Telegram…",
        "pt-BR": "Verificando o token com o Telegram…",
    },
    "telegram.pair.unreachable": {
        "en": "✗ Could not reach Telegram — check your internet connection.\n({exc})",
        "pt-BR": "✗ Não foi possível conectar ao Telegram — verifique sua conexão com a internet.\n({exc})",
    },
    "telegram.pair.rejected": {
        "en": "✗ Token rejected by Telegram — re-check it with @BotFather.",
        "pt-BR": "✗ Token rejeitado pelo Telegram — confira novamente com o @BotFather.",
    },
    "telegram.pair.connected": {
        "en": (
            "✓ Connected to @{username}\n\n"
            "Now open this link and press Start:\n{link}\n\n"
            "Waiting for you to press Start…"
        ),
        "pt-BR": (
            "✓ Conectado a @{username}\n\n"
            "Agora abra este link e toque em Iniciar:\n{link}\n\n"
            "Aguardando você tocar em Iniciar…"
        ),
    },
    "telegram.pair.already_running": {
        "en": (
            "✗ This bot looks like it's already running elsewhere, so Telegram "
            "won't let setup read its messages. Stop that instance, or just type "
            "your chat ID below."
        ),
        "pt-BR": (
            "✗ Este bot parece já estar em execução em outro lugar, então o "
            "Telegram não deixa a configuração ler as mensagens dele. Pare aquela "
            "instância ou apenas digite seu chat ID abaixo."
        ),
    },
    "telegram.pair.timeout": {
        "en": (
            "Timed out waiting for Start. Press Start and click the button "
            "again, or just type your chat ID below."
        ),
        "pt-BR": (
            "Tempo esgotado aguardando o Iniciar. Toque em Iniciar e clique no "
            "botão novamente, ou apenas digite seu chat ID abaixo."
        ),
    },
    "telegram.pair.got_chat": {
        "en": "✓ Got your chat ID ({chat_id}).{extra} You're set — press Next.",
        "pt-BR": "✓ Chat ID obtido ({chat_id}).{extra} Tudo certo — toque em Avançar.",
    },
    "telegram.pair.added_admin": {
        "en": " Added you (user {user_id}) as an admin.",
        "pt-BR": " Você (usuário {user_id}) foi adicionado como administrador.",
    },
    "telegram.err.token": {
        "en": "Bot token: {err}",
        "pt-BR": "Token do bot: {err}",
    },
    "telegram.err.chat": {
        "en": "Admin chat ID: {err}",
        "pt-BR": "Chat ID do administrador: {err}",
    },
    # --- whatsapp -----------------------------------------------------------
    "whatsapp.title": {
        "en": "WhatsApp bridge (optional output transport)",
        "pt-BR": "Ponte do WhatsApp (transporte de saída opcional)",
    },
    "whatsapp.intro": {
        "en": (
            "WhatsApp is an optional, output-only channel. Leave it off for a "
            "Telegram-only setup — you can enable it later by re-running setup."
        ),
        "pt-BR": (
            "O WhatsApp é um canal opcional, somente de saída. Deixe desligado "
            "para uma configuração só com Telegram — você pode ativá-lo depois "
            "executando a configuração novamente."
        ),
    },
    "whatsapp.enable_label": {
        "en": "Enable WhatsApp output?",
        "pt-BR": "Ativar saída do WhatsApp?",
    },
    "whatsapp.bridge_path": {
        "en": "Bridge binary path",
        "pt-BR": "Caminho do binário da ponte",
    },
    "whatsapp.bridge_port": {"en": "Bridge port", "pt-BR": "Porta da ponte"},
    "whatsapp.callback_port": {
        "en": "OctoOps callback port",
        "pt-BR": "Porta de retorno do OctoOps",
    },
    "whatsapp.admins_label": {
        "en": (
            "Admin WhatsApp numbers for the startup message (comma/space "
            "separated, digits only e.g. 5511999998888 — optional)"
        ),
        "pt-BR": (
            "Números de WhatsApp do administrador para a mensagem de início "
            "(separados por vírgula/espaço, só dígitos, ex.: 5511999998888 — "
            "opcional)"
        ),
    },
    "whatsapp.inbound_intro": {
        "en": (
            "Optional inbound: let whitelisted WhatsApp numbers message the brain "
            "(/ask). They can only ever reach the brain — never any other command."
        ),
        "pt-BR": (
            "Entrada opcional: permita que números de WhatsApp na lista de "
            "permissões falem com o cérebro (/ask). Eles só conseguem acessar o "
            "cérebro — nunca qualquer outro comando."
        ),
    },
    "whatsapp.inbound_label": {
        "en": "Enable inbound (whitelisted numbers → brain)?",
        "pt-BR": "Ativar entrada (números permitidos → cérebro)?",
    },
    "whatsapp.allow_label": {
        "en": "Allowed WhatsApp numbers (comma/space separated)",
        "pt-BR": "Números de WhatsApp permitidos (separados por vírgula/espaço)",
    },
    "whatsapp.role_label": {
        "en": "Role for inbound users (viewer/operator/admin)",
        "pt-BR": "Papel dos usuários de entrada (viewer/operator/admin)",
    },
    "whatsapp.err.path": {
        "en": "Bridge path: {err}",
        "pt-BR": "Caminho da ponte: {err}",
    },
    "whatsapp.err.bridge_port": {
        "en": "Bridge port: {err}",
        "pt-BR": "Porta da ponte: {err}",
    },
    "whatsapp.err.callback_port": {
        "en": "Callback port: {err}",
        "pt-BR": "Porta de retorno: {err}",
    },
    "whatsapp.err.ports_differ": {
        "en": "Bridge port and callback port must differ",
        "pt-BR": "A porta da ponte e a porta de retorno devem ser diferentes",
    },
    "whatsapp.err.inbound_role": {
        "en": "Inbound role: {err}",
        "pt-BR": "Papel de entrada: {err}",
    },
    "whatsapp.err.need_allow": {
        "en": "Add at least one allowed WhatsApp number, or turn inbound off",
        "pt-BR": "Adicione pelo menos um número de WhatsApp permitido, ou desligue a entrada",
    },
    # --- core settings ------------------------------------------------------
    "core.title": {"en": "Core settings", "pt-BR": "Configurações principais"},
    "core.timezone": {"en": "Timezone", "pt-BR": "Fuso horário"},
    "core.tz_custom_option": {
        "en": "Custom (type below)…",
        "pt-BR": "Personalizado (digite abaixo)…",
    },
    "core.tz_custom_label": {
        "en": "Custom IANA timezone (only used when 'Custom' is selected)",
        "pt-BR": "Fuso horário IANA personalizado (usado apenas quando 'Personalizado' está selecionado)",
    },
    "core.tz_placeholder": {
        "en": "e.g. America/Sao_Paulo",
        "pt-BR": "ex.: America/Sao_Paulo",
    },
    "core.allowed_label": {
        "en": "Allowed Telegram user IDs (space/comma separated)",
        "pt-BR": "IDs de usuário do Telegram permitidos (separados por espaço/vírgula)",
    },
    "core.operators_label": {
        "en": "Operator user IDs",
        "pt-BR": "IDs de usuário operadores",
    },
    "core.admins_label": {
        "en": "Admin user IDs",
        "pt-BR": "IDs de usuário administradores",
    },
    "core.default_role": {
        "en": "Default role (for allowed users)",
        "pt-BR": "Papel padrão (para usuários permitidos)",
    },
    "core.log_file": {"en": "Log file path", "pt-BR": "Caminho do arquivo de log"},
    "core.label.allowed": {"en": "Allowed", "pt-BR": "Permitidos"},
    "core.label.operator": {"en": "Operator", "pt-BR": "Operadores"},
    "core.label.admin": {"en": "Admin", "pt-BR": "Administradores"},
    "core.err.ids": {"en": "{label} IDs: {err}", "pt-BR": "IDs de {label}: {err}"},
    "core.err.role": {"en": "Default role: {err}", "pt-BR": "Papel padrão: {err}"},
    "core.err.log_file": {
        "en": "Log file: {err}",
        "pt-BR": "Arquivo de log: {err}",
    },
    # --- module selection ---------------------------------------------------
    "modules.title": {"en": "Module selection", "pt-BR": "Seleção de módulos"},
    "modules.none": {
        "en": "No modules discovered.",
        "pt-BR": "Nenhum módulo encontrado.",
    },
    "modules.check": {
        "en": "Check the modules to enable:",
        "pt-BR": "Marque os módulos para ativar:",
    },
    "modules.hint": {
        "en": "↑/↓ to move · Space or click to toggle · all enabled by default",
        "pt-BR": "↑/↓ para mover · Espaço ou clique para alternar · todos ativos por padrão",
    },
    "modules.failed": {
        "en": "⚠ {name} failed to load: {error}",
        "pt-BR": "⚠ {name} falhou ao carregar: {error}",
    },
    # --- module configuration -----------------------------------------------
    "module_config.title": {
        "en": "Module configuration",
        "pt-BR": "Configuração de módulos",
    },
    "module_config.required": {"en": "required", "pt-BR": "obrigatório"},
    "module_config.optional": {"en": "optional", "pt-BR": "opcional"},
    "module_config.field_label": {
        "en": "{label} ({req}) — {description}",
        "pt-BR": "{label} ({req}) — {description}",
    },
    "module_config.err": {
        "en": "{module}.{key}: {err}",
        "pt-BR": "{module}.{key}: {err}",
    },
    # --- Windows Task Scheduler ---------------------------------------------
    "task_scheduler.title": {
        "en": "Windows Task Scheduler",
        "pt-BR": "Agendador de Tarefas do Windows",
    },
    "task_scheduler.intro": {
        "en": (
            "Register OctoOps to start automatically at boot, running as SYSTEM, "
            "restarting on failure (10x / 1 min)? "
            "Logs: logs\\octoops.log (app) and logs\\octoops-stdout.log (startup errors)."
        ),
        "pt-BR": (
            "Registrar o OctoOps para iniciar automaticamente na inicialização, "
            "executando como SYSTEM, reiniciando em caso de falha (10x / 1 min)? "
            "Logs: logs\\octoops.log (app) e logs\\octoops-stdout.log (erros de início)."
        ),
    },
    "task_scheduler.checkbox": {
        "en": "Register the boot task",
        "pt-BR": "Registrar a tarefa de inicialização",
    },
    # --- summary ------------------------------------------------------------
    "summary.title": {"en": "Review & confirm", "pt-BR": "Revisar e confirmar"},
    "summary.intro": {
        "en": "This config.toml will be written (secrets hidden below):",
        "pt-BR": "Este config.toml será gravado (segredos ocultos abaixo):",
    },
    # --- validators (state.py). English text is byte-stable: existing tests
    # assert on it directly (e.g. == "required").
    "validate.required": {"en": "required", "pt-BR": "obrigatório"},
    "validate.bot_token_form": {
        "en": "expected the form 123456:ABC-...",
        "pt-BR": "use o formato 123456:ABC-...",
    },
    "validate.chat_id_numeric": {
        "en": "must be a numeric Telegram ID",
        "pt-BR": "deve ser um ID numérico do Telegram",
    },
    "validate.user_id_numeric": {
        "en": "{value!r} is not a numeric user ID",
        "pt-BR": "{value!r} não é um ID de usuário numérico",
    },
    "validate.timezone_unknown": {
        "en": "unknown IANA timezone: {value!r}",
        "pt-BR": "fuso horário IANA desconhecido: {value!r}",
    },
    "validate.port_number": {"en": "must be a number", "pt-BR": "deve ser um número"},
    "validate.port_range": {
        "en": "must be between 1 and 65535",
        "pt-BR": "deve estar entre 1 e 65535",
    },
    "validate.role_oneof": {
        "en": "must be one of {roles}",
        "pt-BR": "deve ser um de {roles}",
    },
    "validate.need_user": {
        "en": (
            "Authorize at least one user — add your Telegram user ID as an Admin "
            "(the Telegram step can capture it for you)."
        ),
        "pt-BR": (
            "Autorize pelo menos um usuário — adicione seu ID de usuário do "
            "Telegram como Administrador (a etapa do Telegram pode capturá-lo "
            "para você)."
        ),
    },
    "validate.field_integer": {
        "en": "must be an integer",
        "pt-BR": "deve ser um número inteiro",
    },
    "validate.field_boolean": {
        "en": "must be true/false",
        "pt-BR": "deve ser true/false",
    },
    "validate.field_ip": {
        "en": "must be a valid IP address",
        "pt-BR": "deve ser um endereço IP válido",
    },
}


def translate(key: str, lang: str = DEFAULT_LANGUAGE, /, **kwargs: object) -> str:
    """Resolve ``key`` to ``lang``, falling back to English then to the key.

    ``kwargs`` are substituted into the template via ``str.format`` (so templates
    may use ``{name}`` and conversions like ``{value!r}``). A bad/missing field
    yields the unformatted template rather than raising — a missing translation
    must never crash setup.
    """
    entry = _CATALOG.get(key)
    if entry is None:
        text = key
    else:
        text = entry.get(lang) or entry.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
