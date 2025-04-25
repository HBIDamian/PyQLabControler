# PyQLabControler
Python based QLab Controller. Host it on your mac device, and open the url up on your phone or other devices.

## Installation
1. Clone the repository
2. Install the required packages
```bash
pip install -r requirements.txt
```
3. Run the server
```bash
python3 ./PyQLabControler.py
```
It will start the server on port 5000, and show you the IP address of the server. Open that IP address on your phone or other devices to control QLab remotely.

## Other Requirements
- Python 3.x
- QLab 4.x (the project was made for QLab 4.x, and may not work with QLab 5.x due to changes in the AppleScript->QLab API)
- AppleScript enabled on your mac device (this script might ask for elevated permissions to control QLab. Please make sure you read and understand the code before running untrusted code)

## Note
- This project is NOT affiliated with QLab or Figure53 in any way. It is a personal project to control QLab remotely.
- This project is not intended for production use. It was intended to be able to play cues remotely (for example when you're both an actor and a sound designer, and you need to play cues while you're on stage).
- This project utilizes AppleScript to control QLab. It is not a full-fledged QLab controller, and it does not support all the features of QLab.

## License
This project is licensed under the MIT License. See the LICENSE file for more details.
> [!NOTE]  
> I did use Copilot that did do like 80% of the heavy lifting. I have absolutely no clue where the bits and pieces of code came from. If you see your code in here, please let me know and I will add you to the credits.


## Contributing
If you want to contribute to this project, feel free to open an issue or a pull request. I will be happy to review it and merge it if it is useful.