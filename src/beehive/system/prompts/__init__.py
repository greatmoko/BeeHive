"""统一提示词访问入口；内容按用途维护。"""

from beehive.system.prompts.chat import (
    TOKBEE_SYSTEM_PROMPT,
    ROLE_GENERATION_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    MEMORY_SUMMARY_SYSTEM_PROMPT,
    CHAT_TITLE_SYSTEM_PROMPT,
    project_metadata_system_prompt,
)

from beehive.system.prompts.agent import (
    ASK_USER_TOOL_DESCRIPTION,
    EXPERIENCE_TOOL_DESCRIPTION,
    WOKBEE_CHAT_SYSTEM_PROMPT,
    WOKBEE_RUN_SYSTEM_PROMPT,
    DEZIBEE_SYSTEM_PROMPT,
)

from beehive.system.prompts.design_context import (
    build_design_prompt,
)

from beehive.system.prompts.automation import (
    AUTOBEE_TEXT_SYSTEM_PROMPT,
    AUTOBEE_CONFIG_SYSTEM_PROMPT,
)

from beehive.system.prompts.pipeline import PIPELINE_RESUME_PROMPT

from beehive.system.prompts.experience import (
    EXPERIENCE_SUMMARY_SYSTEM_PROMPT,
    EXPERIENCE_UPDATE_SYSTEM_PROMPT,
)



def static_system_prompt(*, mode: str) -> str:
    """会话级静态 system；不加入日期、项目数据或其它易变内容。"""
    if mode == "design":
        return DEZIBEE_SYSTEM_PROMPT
    if mode == "chat":
        return WOKBEE_CHAT_SYSTEM_PROMPT
    return WOKBEE_RUN_SYSTEM_PROMPT
