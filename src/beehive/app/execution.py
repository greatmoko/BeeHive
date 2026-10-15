"""应用层装配 WokBee 运行器与 AutoBee、DeziBee 专属能力。"""
def create_agent_runner(settings=None, provider_store=None):
    from beehive.features.wokbee.execution.runner_execution import AgentRunner
    from beehive.features.autobee.engine.agent_tools import build_autobee_tools
    from beehive.features.dezibee.core.execution_adapter import (
        configure_design_write_validator, prepare_design_question, prepare_design_root,
    )
    return AgentRunner(settings, provider_store,
                       task_tools_factory=build_autobee_tools,
                       design_write_validator=configure_design_write_validator,
                       design_question_preparer=prepare_design_question,
                       design_prepare_root=prepare_design_root)
