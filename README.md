# PyQLabControler
 Python based QLab Controller. Host it on your mac device, and open the url up on your phone or other device


## Installation
1. Clone the repository
2. Install the required packages
```bash
pip install -r requirements.txt
```
3. Run the server
```bash
python qlab.py
```
It will start the server on port 5000, and show you the IP address of the server. Open that IP address on your phone or other device to control QLab.

## Security Note
- This is a very basic implementation, and does not have any security features. It is recommended to run this on a local network, and not expose it to the internet.
- It currently  screenshots your entire desktop. every second. This is not ideal, and should be changed to only screenshot the QLab window. I'll do this when I can figure out a Mac-only way to do it.


## Braindead Anti-To-Do List
A list of things I want to do, but for one reason or another, I am unable to do. This is a list of things that I want to do, but can't do. I'm putting it here so I can remember to do it later.

- [ ] Add a way to change the IP address of the server from the web interface
- Add Active Cue/Selcted Cue/Cue List information to the web interface (tried a million times)
  - Add a way to change the selected cue from the web interface