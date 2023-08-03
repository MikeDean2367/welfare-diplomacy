"""
Represents different agent prompting systems (e.g. GPT, 🤗 Transformers, random).

A given agent should take in information about the game and a power, prompt
an underlying model for a response, and return back the extracted response. 
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import json
import random
import time
from typing import Optional

from diplomacy import Power, Game
import wandb

from backends import OpenAIChatBackend
import prompts


@dataclass
class AgentResponse:
    """A response from an agent for a single turn of actions."""

    model_name: str  # Name of the generating model.
    reasoning: str  # Private reasoning to generate the response.
    orders: list[str]  # Orders to execute.
    messages: dict[str, str]  # Messages to send to other powers.
    system_prompt: str  # System prompt
    user_prompt: Optional[str]  # User prompt if available
    prompt_tokens: int  # Number of tokens in prompt
    completion_tokens: int  # Number of tokens in completion
    total_tokens: int  # Total number of tokens in prompt and completion
    completion_time_sec: float  # Time to generate completion in seconds


class Agent(ABC):
    """Base agent class."""

    @abstractmethod
    def respond(
        self,
        power: Power,
        game: Game,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
    ) -> AgentResponse:
        """Prompt the model for a response."""


class RandomAgent(Agent):
    """Takes random actions and sends 1 random message."""

    def respond(
        self,
        power: Power,
        game: Game,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
    ) -> AgentResponse:
        """Randomly generate orders and messages."""
        # For each power, randomly sampling a valid order
        power_orders = []
        for loc in game.get_orderable_locations(power.name):
            if possible_orders[loc]:
                # Sort for determinism
                possible_orders[loc].sort()

                # If this is a disbandable unit in an adjustment phase in welfare,
                # then randomly choose whether to disband or not
                if (
                    "ADJUSTMENTS" in str(game.phase)
                    and " D" in possible_orders[loc][0][-2:]
                    and game.welfare
                ):
                    power_orders.append(
                        random.choice(["WAIVE", possible_orders[loc][0]])
                    )
                else:
                    power_orders.append(random.choice(possible_orders[loc]))

        # For debugging prompting
        system_prompt = prompts.get_system_prompt(
            power, game, current_message_round, max_message_rounds, final_game_year
        )
        user_prompt = prompts.get_user_prompt(power, game, possible_orders)

        # Randomly sending a message to another power
        other_powers = [p for p in game.powers if p != power.name]
        recipient = random.choice(other_powers)
        message = f"Hello {recipient}! I'm {power.name} contacting you on turn {game.get_current_phase()}. Here's a random number: {random.randint(0, 100)}."

        # Sleep to allow wandb to catch up
        sleep_time = (
            0.0
            if isinstance(wandb.run.mode, wandb.sdk.lib.disabled.RunDisabled)
            else 0.3
        )
        time.sleep(sleep_time)

        return AgentResponse(
            model_name="RandomAgent",
            reasoning="Randomly generated orders and messages.",
            orders=power_orders,
            messages={recipient: message},
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            completion_time_sec=sleep_time,
        )


class ForceRetreatAgent(Agent):
    """Contrive a situation to put the game in a retreats phase."""

    def respond(
        self,
        power: Power,
        game: Game,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
    ) -> AgentResponse:
        """Get to a retreats phase."""
        # For each power, randomly sampling a valid order
        power_orders = []

        if game.get_current_phase() == "S1901M":
            if power.name == "GERMANY":
                power_orders = ["A MUN TYR"]
        elif game.get_current_phase() == "F1901M":
            if power.name == "AUSTRIA":
                power_orders = ["A VIE S A VEN TYR"]
            elif power.name == "ITALY":
                power_orders = ["A VEN TYR"]

        # For debugging prompting
        system_prompt = prompts.get_system_prompt(
            power, game, current_message_round, max_message_rounds, final_game_year
        )
        user_prompt = prompts.get_user_prompt(power, game, possible_orders)

        # Sleep to allow wandb to catch up
        sleep_time = (
            0.0
            if isinstance(wandb.run.mode, wandb.sdk.lib.disabled.RunDisabled)
            else 0.3
        )
        time.sleep(sleep_time)

        return AgentResponse(
            model_name="ForceRetreatAgent",
            reasoning="Forcing the game into a retreats phase.",
            orders=power_orders,
            messages={},
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            completion_time_sec=sleep_time,
        )


class OpenAIChatAgent(Agent):
    """Uses OpenAI Chat to generate orders and messages."""

    def __init__(self, model_name: str, **kwargs):
        self.backend = OpenAIChatBackend(model_name)
        self.temperature = kwargs.pop("temperature", 0.7)
        self.top_p = kwargs.pop("top_p", 1.0)

    def respond(
        self,
        power: Power,
        game: Game,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
    ) -> AgentResponse:
        """Prompt the model for a response."""
        system_prompt = prompts.get_system_prompt(
            power, game, current_message_round, max_message_rounds, final_game_year
        )
        user_prompt = prompts.get_user_prompt(power, game, possible_orders)
        response = self.backend.complete(
            system_prompt, user_prompt, temperature=self.temperature, top_p=self.top_p
        )
        completion = response.choices[0].message.content  # type: ignore
        completion = json.loads(completion, strict=False)
        # Turn recipients in messages into ALLCAPS for the engine
        completion["messages"] = {
            recipient.upper(): message
            for recipient, message in completion["messages"].items()
        }
        assert "usage" in response, "OpenAI response does not contain usage"
        usage = response["usage"]  # type: ignore
        completion_time_sec = response.response_ms / 1000.0  # type: ignore
        return AgentResponse(
            model_name=self.backend.model_name,
            reasoning=completion["reasoning"],
            orders=completion["orders"],
            messages=completion["messages"],
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            completion_time_sec=completion_time_sec,
        )


def model_name_to_agent(model_name: str, **kwargs) -> Agent:
    """Given a model name, return an instantiated corresponding agent."""
    model_name = model_name.lower()
    if model_name == "random":
        return RandomAgent()
    elif model_name == "retreats":
        return ForceRetreatAgent()
    elif "gpt-4" in model_name or "gpt-3.5" in model_name:
        return OpenAIChatAgent(model_name, **kwargs)
    else:
        raise ValueError(f"Unknown model name: {model_name}")