"""
Language model scaffolding to play Diplomacy.


"""

import argparse
import logging
import os

from diplomacy import Game, Message
from diplomacy.utils.export import to_saved_game_format
import wandb

import constants
import utils
from prompter import Prompter, model_name_to_prompter


def main():
    """Simulate a game of Diplomacy with the given parameters."""
    # Parse args
    args = parse_args()

    # Initialize seed, wandb, game, logger, and prompter
    utils.set_seed(args.seed)

    wandb.init(
        entity=args.entity,
        project=args.project,
        save_code=True,
        config=vars(args),
        mode="disabled" if args.disable_wandb else "online",
        settings=wandb.Settings(code_dir="experiments"),
    )
    assert wandb.run is not None
    game = Game(map_name=args.map_name)
    logger = logging.getLogger(__name__)
    logging.basicConfig()
    logger.setLevel(args.log_level)

    prompter: Prompter = model_name_to_prompter(args.model)

    logger.info(
        f"Starting game with map {args.map_name} and model {args.model} ending after {args.max_years} years with {args.max_message_rounds} message rounds."
    )

    while not game.is_game_done:
        logger.info(f"Beginning phase {game.get_current_phase()}")
        # Cache the list of possible orders for all locations
        possible_orders = game.get_all_possible_orders()

        total_num_orders = 0
        total_num_valid_orders = 0
        sum_valid_order_ratio = 0.0
        total_message_sent = 0
        sum_completion_time_sec = 0.0
        for power_name, power in game.powers.items():
            # Prompting the model for a response
            prompter_response = prompter.respond(
                power, game, possible_orders, args.max_message_rounds, args.max_years
            )
            sum_completion_time_sec += prompter_response.completion_time_sec
            logger.info(
                f"Prompter {prompter_response.model_name} took {prompter_response.completion_time_sec:.2f}s to respond.\nResponse:\n{prompter_response}"
            )

            # Check how many of the orders were valid
            num_valid_orders = 0
            for order in prompter_response.orders:
                word = order.split()
                unit, destination = " ".join(word[:2]), " ".join(word[2:])
                if game._valid_order(power, unit, destination):
                    num_valid_orders += 1
            num_orders = len(prompter_response.orders)
            valid_order_ratio = num_valid_orders / len(prompter_response.orders)
            logger.info(
                f"{power_name} valid orders: {num_valid_orders}/{num_orders} = {valid_order_ratio * 100.0:.2f}%"
            )
            total_num_orders += num_orders
            total_num_valid_orders += num_valid_orders
            sum_valid_order_ratio += valid_order_ratio

            # Set orders
            game.set_orders(power_name, prompter_response.orders)

            # Send messages
            for recipient, message in prompter_response.messages.items():
                game.add_message(
                    Message(
                        sender=power_name,
                        recipient=recipient,
                        message=message,
                        phase=game.get_current_phase(),
                    )
                )
                total_message_sent += 1

        # Processing the game to move to the next phase
        game.process()

        # Check whether to end the game
        phase = game.get_phase_history()[-1]
        if utils.get_game_year(phase) > args.max_years:
            game._finish([])

        # Log to Weights & Biases
        rendered = game.render(incl_abbrev=True)
        log_object = {
            "meta/year_fractional": utils.get_game_fractional_year(phase),
            "board/rendering": wandb.Html(rendered),
            "orders/num_total": total_num_orders,
            "orders/num_valid": total_num_valid_orders,
            "orders/valid_ratio_total_avg": total_num_valid_orders / total_num_orders,
            "orders/valid_ratio_avg_avg": sum_valid_order_ratio / len(game.powers),
            "messages/num_total": total_message_sent,
            "messages/num_avg": total_message_sent / len(game.powers),
            "model/completion_time_sec_avg": sum_completion_time_sec / len(game.powers),
        }
        for power in game.powers.values():
            short_name = power.name[:3]
            if game.phase_type == "A":
                log_object[f"score/units/{short_name}"] = len(power.units)
                log_object[f"score/welfare/{short_name}"] = power.welfare_points
            else:
                log_object[f"score/centers/{short_name}"] = len(power.centers)

        wandb.log(log_object)

        # Print some information about the game
        score_string = " ".join(
            [
                f"{power.abbrev}: {len(power.centers)}/{len(power.units)}/{power.welfare_points}"
                for power in game.powers.values()
            ]
        )
        logger.info(f"{phase.name} C/U/W: {score_string}")

    # Exporting the game to disk to visualize (game is appended to file)
    # Alternatively, we can do >> file.write(json.dumps(to_saved_game_format(game)))
    if not args.no_save:
        if not os.path.exists(args.output_folder):
            os.makedirs(args.output_folder)
        output_id = "debug" if args.disable_wandb else wandb.run.id
        to_saved_game_format(
            game, output_path=os.path.join(args.output_folder, f"game-{output_id}.json")
        )


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Simulate a game of Diplomacy with the given parameters."
    )
    parser.add_argument("--log_level", dest="log_level", default="INFO")
    parser.add_argument("--map", dest="map_name", default="standard_welfare")
    parser.add_argument("--output_folder", dest="output_folder", default="games")
    parser.add_argument("--no_save", dest="no_save", action="store_true")
    parser.add_argument("--seed", dest="seed", type=int, default=0, help="random seed")
    parser.add_argument("--entity", dest="entity", default="gabrielmukobi")
    parser.add_argument("--project", dest="project", default=constants.WANDB_PROJECT)
    parser.add_argument("--disable_wandb", dest="disable_wandb", action="store_true")
    parser.add_argument("--max_years", dest="max_years", type=int, default=10)
    parser.add_argument(
        "--max_message_rounds", dest="max_message_rounds", type=int, default=1
    )
    parser.add_argument("--model", dest="model", default="gpt-4-0613")

    args = parser.parse_args()
    return args


if __name__ == "__main__":
    main()
