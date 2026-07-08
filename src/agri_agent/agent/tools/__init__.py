"""Tool registry — maps YAML config names to LangChain tool objects."""

from langchain_core.tools import BaseTool

from agri_agent.agent.tools.calculator import calculator
from agri_agent.agent.tools.platform import propose_action
from agri_agent.agent.tools.dispatch import (
    dispatch_order,
    get_dispatch_rules,
    get_order_details,
    get_pending_orders,
    recommend_dispatch,
)
from agri_agent.agent.tools.outreach import filter_prospects, list_retailers, send_email
from agri_agent.agent.tools.search import web_search
from agri_agent.agent.tools.sandhar.attendance import (
    sandhar_get_attendance_summary,
    sandhar_get_present_operators,
    sandhar_check_certification_expiry,
    sandhar_find_qualified_operators,
    sandhar_get_operator_skills,
    sandhar_get_crossskill_candidates,
)
from agri_agent.agent.tools.sandhar.workorders import (
    sandhar_list_lines,
    sandhar_get_open_work_orders,
    sandhar_get_work_order_detail,
    sandhar_rank_work_orders,
)
from agri_agent.agent.tools.sandhar.constraints import (
    sandhar_get_machine_status,
    sandhar_check_material_availability,
    sandhar_get_quality_holds,
    sandhar_get_constraint_summary,
)
from agri_agent.agent.tools.sandhar.planning import (
    sandhar_calculate_planned_qty,
    sandhar_allocate_line,
    sandhar_save_plan_header,
    sandhar_create_alert,
    sandhar_propose_plan_for_review,
)
from agri_agent.agent.tools.sandhar.plan_refiner import (
    sandhar_refine_get_plan,
    sandhar_refine_update_qty,
    sandhar_refine_move_wo,
    sandhar_refine_add_wo,
    sandhar_refine_remove_wo,
    sandhar_refine_explain_constraint,
)
from agri_agent.agent.tools.propguru.deals import (
    propguru_get_deal,
    propguru_get_property_details,
    propguru_list_deals,
)
from agri_agent.agent.tools.propguru.evaluation import (
    propguru_get_criteria,
    propguru_get_market_comp,
    propguru_create_evaluation_report,
    propguru_save_evaluation_score,
    propguru_calculate_price,
    propguru_set_base_price,
    propguru_score_proximity,
    propguru_propose_evaluation,
)

_TOOL_REGISTRY: dict[str, BaseTool] = {
    "calculator": calculator,
    "web_search": web_search,
    "list_retailers": list_retailers,
    "filter_prospects": filter_prospects,
    "send_email": send_email,
    "get_pending_orders": get_pending_orders,
    "get_order_details": get_order_details,
    "get_dispatch_rules": get_dispatch_rules,
    "dispatch_order": dispatch_order,
    "recommend_dispatch": recommend_dispatch,   # kept for backward compat
    "propose_action": propose_action,
    "sandhar_get_attendance_summary": sandhar_get_attendance_summary,
    "sandhar_get_present_operators": sandhar_get_present_operators,
    "sandhar_check_certification_expiry": sandhar_check_certification_expiry,
    "sandhar_find_qualified_operators": sandhar_find_qualified_operators,
    "sandhar_get_operator_skills": sandhar_get_operator_skills,
    "sandhar_get_crossskill_candidates": sandhar_get_crossskill_candidates,
    "sandhar_list_lines": sandhar_list_lines,
    "sandhar_get_open_work_orders": sandhar_get_open_work_orders,
    "sandhar_get_work_order_detail": sandhar_get_work_order_detail,
    "sandhar_rank_work_orders": sandhar_rank_work_orders,
    "sandhar_get_machine_status": sandhar_get_machine_status,
    "sandhar_check_material_availability": sandhar_check_material_availability,
    "sandhar_get_quality_holds": sandhar_get_quality_holds,
    "sandhar_get_constraint_summary": sandhar_get_constraint_summary,
    "sandhar_calculate_planned_qty": sandhar_calculate_planned_qty,
    "sandhar_allocate_line": sandhar_allocate_line,
    "sandhar_save_plan_header": sandhar_save_plan_header,
    "sandhar_create_alert": sandhar_create_alert,
    "sandhar_propose_plan_for_review": sandhar_propose_plan_for_review,
    "sandhar_refine_get_plan": sandhar_refine_get_plan,
    "sandhar_refine_update_qty": sandhar_refine_update_qty,
    "sandhar_refine_move_wo": sandhar_refine_move_wo,
    "sandhar_refine_add_wo": sandhar_refine_add_wo,
    "sandhar_refine_remove_wo": sandhar_refine_remove_wo,
    "sandhar_refine_explain_constraint": sandhar_refine_explain_constraint,
    # ── Propguru ──────────────────────────────────────────────────────────────
    "propguru_get_deal": propguru_get_deal,
    "propguru_get_property_details": propguru_get_property_details,
    "propguru_list_deals": propguru_list_deals,
    "propguru_get_criteria": propguru_get_criteria,
    "propguru_get_market_comp": propguru_get_market_comp,
    "propguru_create_evaluation_report": propguru_create_evaluation_report,
    "propguru_save_evaluation_score": propguru_save_evaluation_score,
    "propguru_calculate_price": propguru_calculate_price,
    "propguru_set_base_price": propguru_set_base_price,
    "propguru_score_proximity": propguru_score_proximity,
    "propguru_propose_evaluation": propguru_propose_evaluation,
}


def get_tools_for_config(tool_configs: list) -> list[BaseTool]:
    """Return enabled tool instances for a list of ToolConfig objects."""
    tools = []
    for tc in tool_configs:
        if tc.enabled and tc.name in _TOOL_REGISTRY:
            tools.append(_TOOL_REGISTRY[tc.name])
    return tools


def list_available_tools() -> list[str]:
    return list(_TOOL_REGISTRY.keys())


def list_tools_with_descriptions() -> list[dict]:
    """Return name + description for every tool in the registry."""
    return [
        {"name": name, "description": tool.description or ""}
        for name, tool in _TOOL_REGISTRY.items()
    ]
