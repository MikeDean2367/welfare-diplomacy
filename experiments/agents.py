"""
Represents different agent prompting systems (e.g. GPT, 🤗 Transformers, random).

A given agent should take in information about the game and a power, prompt
an underlying model for a response, and return back the extracted response. 
"""

from abc import ABC, abstractmethod
import json
import random
import time
import yaml

from diplomacy import Power, Game
import wandb

from backends import ClaudeCompletionBackend, OpenAIChatBackend, OpenAICompletionBackend
from data_types import (
    AgentResponse,
    BackendResponse,
    MessageSummaryHistory,
    PromptAblation,
)
import prompts


class AgentCompletionError(ValueError):
    """Raised when an agent fails to complete a prompt."""


class Agent(ABC):
    """Base agent class."""

    def __init__(self, **_):
        """Base init to ignore unused kwargs."""

    @abstractmethod
    def respond(
        self,
        power: Power,
        game: Game,
        message_summary_history: MessageSummaryHistory,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
        prompt_ablations: list[PromptAblation],
    ) -> AgentResponse:
        """Prompt the model for a response."""


class RandomAgent(Agent):
    """Takes random actions and sends 1 random message."""

    def respond(
        self,
        power: Power,
        game: Game,
        message_summary_history: MessageSummaryHistory,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
        prompt_ablations: list[PromptAblation],
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
        # Randomly add an invalid order
        if random.random() < 0.1:
            power_orders.append("Random invalid order")

        # More randomly raise a completion error
        if random.random() < 0.05:
            raise AgentCompletionError("Randomly raised completion error")

        # For debugging prompting
        system_prompt = prompts.get_system_prompt(
            power,
            game,
            current_message_round,
            max_message_rounds,
            final_game_year,
            prompt_ablations,
        )
        user_prompt = prompts.get_user_prompt(
            power, game, message_summary_history, possible_orders, prompt_ablations
        )

        # Randomly sending a message to another power
        other_powers = [p for p in game.powers if p != power.name] + ["GLOBAL"]
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
            messages={recipient: message} if not game.no_press else {},
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            completion_time_sec=sleep_time,
        )


class ManualAgent(Agent):
    """Manually specify orders with --manual_orders_path."""

    def __init__(self, manual_orders_path: str, **_):
        """Load the manual orders."""
        assert manual_orders_path
        self.manual_orders_path = manual_orders_path
        with open(manual_orders_path, "r") as file:
            self.manual_orders: dict[str, list[str]] = yaml.safe_load(file)

    def respond(
        self,
        power: Power,
        game: Game,
        message_summary_history: MessageSummaryHistory,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
        prompt_ablations: list[PromptAblation],
    ) -> AgentResponse:
        """Submit the specified orders if available."""
        power_orders = []

        current_phase = game.get_current_phase()
        if current_phase in self.manual_orders:
            orders_list = self.manual_orders[current_phase]
            for order in orders_list:
                unit_type = order.split()[0]
                loc = order.split()[1]
                if unit_type + " " + loc in power.units:
                    assert (
                        order in possible_orders[loc]
                    ), f"Manual order {order} not in possible orders for power {power.name}:\n{possible_orders[loc]}"
                    power_orders.append(order)

        # For debugging prompting
        system_prompt = prompts.get_system_prompt(
            power,
            game,
            current_message_round,
            max_message_rounds,
            final_game_year,
            prompt_ablations,
        )
        user_prompt = prompts.get_user_prompt(
            power, game, message_summary_history, possible_orders, prompt_ablations
        )

        # Sleep to allow wandb to catch up
        sleep_time = (
            0.0
            if isinstance(wandb.run.mode, wandb.sdk.lib.disabled.RunDisabled)
            else 0.3
        )
        time.sleep(sleep_time)

        return AgentResponse(
            model_name=f"ManualAgent ({self.manual_orders_path})",
            reasoning="Specifying.",
            orders=power_orders,
            messages={},
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            completion_time_sec=sleep_time,
        )


class APIAgent(Agent):
    """Uses OpenAI/Claude Chat/Completion to generate orders and messages."""

    def __init__(self, model_name: str, **kwargs):
        # Decide whether it's a chat or completion model
        if (
            "gpt-4-base" in model_name
            or "text-" in model_name
            or "davinci" in model_name
        ):
            self.backend = OpenAICompletionBackend(model_name)
        elif "claude" in model_name:
            self.backend = ClaudeCompletionBackend(model_name)
        else:
            self.backend = OpenAIChatBackend(model_name)
        self.model_name = model_name
        self.temperature = kwargs.pop("temperature", 0.7)
        self.top_p = kwargs.pop("top_p", 1.0)

    def respond(
        self,
        power: Power,
        game: Game,
        message_summary_history: MessageSummaryHistory,
        possible_orders: dict[str, list[str]],
        current_message_round: int,
        max_message_rounds: int,
        final_game_year: int,
        prompt_ablations: list[PromptAblation],
    ) -> AgentResponse:
        """Prompt the model for a response."""
        system_prompt = prompts.get_system_prompt(
            power,
            game,
            current_message_round,
            max_message_rounds,
            final_game_year,
            prompt_ablations,
        )
        user_prompt = prompts.get_user_prompt(
            power, game, message_summary_history, possible_orders, prompt_ablations
        )
        response: BackendResponse = self.backend.complete(
            system_prompt, user_prompt, temperature=self.temperature, top_p=self.top_p
        )
        try:
            completion = json.loads(response.completion, strict=False)
        except json.JSONDecodeError as exc:
            raise AgentCompletionError(f"JSON Error: {exc}\n\nResponse: {response}")
        try:
            # Extract data from completion
            reasoning = completion["reasoning"]
            orders = completion["orders"]
            # Enforce no messages in no_press
            if game.no_press:
                completion["messages"] = {}
            # Turn recipients in messages into ALLCAPS for the engine
            messages = {}
            for recipient, message in completion["messages"].items():
                if isinstance(message, list):
                    # Handle weird model outputs
                    message = " ".join(message)
                if not isinstance(message, str):
                    # Force each message into a string
                    message = str(message)
                if not message:
                    # Skip empty messages
                    continue
                messages[recipient.upper()] = message
        except Exception as exc:
            raise AgentCompletionError(f"Exception: {exc}\n\nCompletion: {completion}")
        return AgentResponse(
            model_name=self.backend.model_name,
            reasoning=reasoning,
            orders=orders,
            messages=messages,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
            completion_time_sec=response.completion_time_sec,
        )


def model_name_to_agent(model_name: str, **kwargs) -> Agent:
    """Given a model name, return an instantiated corresponding agent."""
    model_name = model_name.lower()
    if model_name == "random":
        return RandomAgent()
    elif model_name == "manual":
        return ManualAgent(**kwargs)
    elif (
        "gpt-" in model_name
        or "davinci-" in model_name
        or "text-" in model_name
        or "claude" in model_name
    ):
        return APIAgent(model_name, **kwargs)
    else:
        raise ValueError(f"Unknown model name: {model_name}")
