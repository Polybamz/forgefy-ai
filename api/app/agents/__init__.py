"""Agent registry. To add an agent: write a module like the two below, then list it here."""
from app.agents.base import Agent
from app.agents.forgefy_requirements import ForgefyRequirementsAgent
from app.agents.vndor_product_description import VndorProductDescriptionAgent

AGENTS: dict[str, Agent] = {a.key: a for a in (ForgefyRequirementsAgent(), VndorProductDescriptionAgent())}
