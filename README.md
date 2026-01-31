# Castorice Bot (雨喵)

A feature-rich QQ Channel Bot built with `botpy`, designed to provide various utility and entertainment functions for server members. The bot features a persona named "雨喵" (Yu Miao), a friendly and slightly tsundere female college student.

## Features

### 🤖 AI Chat & QA
- **General Chat**: Engage in conversations with "雨喵" powered by the DeepSeek API (OpenAI compatible).
- **Server QA**: specialized mode for answering Minecraft server management questions.
  - Usage: `/服务器QA <question>`

### 🎮 Game Utilities
- **Epic Games**: Check for current and upcoming free games on the Epic Games Store.
  - Usage: `/epicfree`
  - Subscription: `订阅epic` (Daily push at 12:00 PM), `epicTD` (Unsubscribe)
- **Steam**: (Module available) Steam related utilities.
- **Minecraft**: Check status of Java Edition servers.
  - Usage: 
    - `/mc绑定 <domain/ip:port>` - Bind a server to the group.
    - `/mc解绑` - Unbind the server.
    - `/mc状态` - Check server status (online/offline, latency, player count, player list).

### 🎲 Entertainment & Tools
- **Blackjack**: Play a game of Blackjack.
- **Crazy Talk**: Fun/random interactions.
- **Dice**: Roll a die.
  - Usage: `/摇骰子`
- **Sleep**: Sleep reminders or tracking (Module available).

## Technical Details

### Architecture
The project is structured around a central `MyClient` class inheriting from `botpy.Client`. Functionality is modularized into separate classes within the `modules/` directory, making it easy to extend and maintain.

- **`main.py`**: Entry point. Initializes the client, loads configuration, and registers event handlers.
- **`modules/`**: Contains specific logic for each feature (e.g., `ai_chat.py`, `epic_games.py`, `minecraft.py`).
- **`data/`**: Stores runtime data (ignored in repo).
- **`config.yaml`**: Configuration file for API keys and secrets (ignored in repo).

### Dependencies
- `botpy`: Official Python SDK for QQ Channel Bot.
- `openai`: For accessing DeepSeek API.
- `mcstatus`: For querying Minecraft server status.
- `requests`: For HTTP requests (Epic Games store data).
- `pytz`: For timezone handling.
- `PyYAML`: For configuration parsing.

## Setup & Installation

1. **Clone the repository**
   ```bash
   git clone <repository_url>
   cd castorice-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configuration**
   Create a `config.yaml` file in the root directory with the following structure:
   ```yaml
   appid: "YOUR_APP_ID"
   secret: "YOUR_APP_SECRET"
   deepseek:
     api_key: "YOUR_DEEPSEEK_API_KEY"
     qa_api_key: "YOUR_QA_API_KEY" # Optional, defaults to api_key
   ```
   *Note: Ensure you have the necessary credentials from the QQ Open Platform and DeepSeek.*

4. **Run the bot**
   ```bash
   python main.py
   ```

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

## License
[MIT](https://choosealicense.com/licenses/mit/)
