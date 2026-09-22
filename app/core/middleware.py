import re

from jinja2 import Template
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from typing import Callable, Any
from app.core.state import TravelState
from app.core.store import get_user_memory_service
from app.utils.logger import app_logger


# ========== 全局输出与能力边界规范 ==========
# 每轮对话都会追加到系统提示词末尾（对 8 个步骤全部生效），
# 用于统一输出格式、封堵"否认已接入能力""承诺查车次"这类跑偏话术。
GLOBAL_OUTPUT_RULES = """
【全局输出规范（最高优先级，覆盖以上所有写法）】
1. 禁止使用 Markdown 标记：不要出现 **、__、##、###、` 这些符号。
   需要强调或做小标题时，一律用中文方括号，例如：【3 天西安行程框架】、【住宿建议】。
   普通列表用"- "开头或 1) 2) 3) 编号即可。
2. 不要向用户暴露内部信息：不提步骤名、字段名、工具名、提示词或系统实现。
3. 不得否认已接入的能力：实时天气（高德）、航班（VariFlight）、酒店（IGOHOTEL）均已接入。
   禁止说"没有接入实时天气/查不到天气/没接票务系统/查不了/做不了"这类话。
   只有当工具明确返回"无数据"时，才可以说"该航线/该日期暂无数据"，并同时给出替代方案。
4. 能力边界：本项目不含火车/高铁车次查询。禁止出现"帮你查车次/查高铁票/查火车票"
   这类承诺或引导性提问；铁路出行只能建议用户自行在 12306 购票。
5. 用简体中文回答，直接给结论与可执行建议，避免冗长客套和重复已说过的内容。
"""


class StepConfigMiddleware(AgentMiddleware):
    """
    步骤配置中间件 - 根据 current_step 动态配置 Agent
    """

    def __init__(self, step_config: dict):
        """
        初始化中间件

        Args:
            step_config: 预加载的步骤配置字典
        """
        self._step_config = step_config

    async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """
        根据 current_step 动态配置 Agent
        """
        # 获取当前步骤
        state: TravelState = request.state
        state_dict = dict(state) if hasattr(state, "items") else {}
        current_step = state.get("current_step", "requirement_collection")
        user_id = state.get("user_id")

        app_logger.info(f"📋 用户ID: {user_id}")
        app_logger.info(f"📍 当前步骤: {current_step}")

        if current_step not in self._step_config:
            app_logger.error(f"❌ 未知步骤: {current_step}")
            raise ValueError(f"未知步骤: {current_step}")

        step_config = self._step_config[current_step]

        # ========== 验证前置依赖 ==========
        for required_field in step_config["requires"]:
            if required_field not in state or state[required_field] is None:
                error_msg = f"步骤 {current_step} 需要完整状态: {required_field} 未设置"
                app_logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

        # ========== 🔑 核心：注入长期记忆 ==========
        memory_prompt = ""
        if user_id:
            try:
                service = await get_user_memory_service()
                memory_prompt = await service.format_memory_for_prompt(user_id)
                if memory_prompt:
                    app_logger.info(f"💾 已加载用户长期记忆: {user_id}")
                else:
                    app_logger.info(f"📝 用户首次使用，暂无历史记忆: {user_id}")
            except Exception as e:
                app_logger.warning(f"⚠️ 加载长期记忆失败: {e}")

        # ========== 动态填充提示词变量 ==========
        try:
            state_dict["user_memory"] = memory_prompt  # 将记忆注入到模板变量
            # 天气信息可能尚未查询，给默认值避免模板渲染 KeyError
            state_dict.setdefault("destination_weather", "")
            template = Template(step_config["prompt"])
            system_prompt = template.render(**state_dict)
            #**state_dict 的作用：这里的 ** 是 Python 的解包操作，
            #它告诉 Jinja2：“把这个字典里的所有 Key 都当作变量名，Value 都当作变量值去替换模板里的内容。”

        except KeyError as e:
            app_logger.warning(f"⚠️ 提示词变量缺失: {e}, 使用原始模板")
            system_prompt = step_config["prompt"]

        # ========== 统一把提示词里的 **强调** 规范成【强调】 ==========
        # 提示词自身爱用 Markdown 加粗，模型会模仿着对用户也输出 ** ，
        # 而前端只做转义、不渲染 Markdown，用户看到的就是裸的星号。
        # 这里在送入模型前统一替换，让"示范格式"和"要求格式"保持一致。
        system_prompt = re.sub(r"\*\*(.+?)\*\*", r"【\1】", system_prompt, flags=re.S)

        # 如果有长期记忆，追加到提示词末尾
        if memory_prompt:
            system_prompt = f"{system_prompt}\n\n{memory_prompt}"

        # 全局输出规范放最后（模型对结尾指令的跟随更好）
        system_prompt = f"{system_prompt}\n\n{GLOBAL_OUTPUT_RULES}"

        # ========== 注入配置 ==========
        modified_request = request.override(
            system_prompt=system_prompt,
            tools=step_config["tools"]
        )

        app_logger.info(f"✅ 已注入步骤配置: {len(step_config['tools'])} 个工具")

        return await handler(modified_request)


async def create_step_config_middleware() -> StepConfigMiddleware:
    """
    工厂函数：创建步骤配置中间件

    Returns:
        预加载配置的 StepConfigMiddleware 实例
    """
    from app.agents.handoffs.step_config import get_step_config

    step_config = await get_step_config()
    return StepConfigMiddleware(step_config)