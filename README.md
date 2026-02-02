# STM32H745 Ethernet Communication Project

This project enables bidirectional Ethernet communication between a laptop and an STM32H745ZI-Q Nucleo board. The board runs a TCP echo server that responds to messages and controls LEDs via simple commands.

## Project Overview

The STM32 board acts as a TCP server listening on port **8080** with the IP address **192.168.1.10**. It provides the following functionality:

- **Echo Server**: Echoes back all messages sent from the laptop
- **Periodic Ping**: Sends "ping" messages every 1 second to all connected clients
- **LED Control**: Toggle LEDs via single-character commands:
  - `g` - Toggle Green LED (PB0)
  - `r` - Toggle Red LED (PB14)
  - `y` - Toggle Yellow LED (PE1)

## Hardware Requirements

- **STM32H745ZI-Q Nucleo Board** (or compatible STM32H7 board)
- **Ethernet cable** (direct connection between laptop and board)
- **USB cable** for programming and power

## Software Requirements

- **STM32CubeIDE 2.0** or later
- **Python 3.x** (for the communication script)
- **Windows** or **macOS** laptop

---

## Laptop IP Configuration

### Windows Setup (GUI Method)

If command-line configuration isn't working, use the Windows GUI:

1. **Open Network Connections**:
   - Press `Win + R`
   - Type `ncpa.cpl`
   - Press `Enter`

2. **Configure Ethernet Adapter**:
   - Right-click on **"Ethernet 3"** (or your active Ethernet adapter)
   - Select **Properties**

3. **Set Static IP**:
   - Select **"Internet Protocol Version 4 (TCP/IPv4)"**
   - Click **Properties**
   - Select **"Use the following IP address"**
   - Enter the following:
     - **IP address**: `192.168.1.1`
     - **Subnet mask**: `255.255.255.0`
     - **Default gateway**: (leave empty)
   - Click **OK** → **OK**

4. **Verify Configuration**:
   - Open Command Prompt or PowerShell
   - Run: `ipconfig`
   - Verify that "Ethernet 3" shows:
     ```
     IPv4 Address. . . . . . . . . . . : 192.168.1.1
     Subnet Mask . . . . . . . . . . . : 255.255.255.0
     ```

### macOS Setup

1. **Open System Settings**:
   - Click the Apple menu → **System Settings** (or **System Preferences** on older macOS)

2. **Navigate to Network**:
   - Click **Network** in the sidebar
   - Select your **Ethernet** connection from the list

3. **Configure IPv4**:
   - Click the **Details...** button (or gear icon)
   - Select the **TCP/IP** tab
   - Change **Configure IPv4** to **Manually**
   - Enter:
     - **IPv4 Address**: `192.168.1.1`
     - **Subnet Mask**: `255.255.255.0`
     - **Router**: (leave empty)
   - Click **OK** or **Apply**

4. **Verify Configuration**:
   - Open Terminal
   - Run: `ifconfig en0` (or `en1`, `en2`, etc. depending on your Ethernet interface)
   - Verify the IP address is `192.168.1.1`

---

## STM32CubeIDE Setup

### 1. Open the Project

1. Launch **STM32CubeIDE 2.0**
2. **File** → **Open Projects from File System...**
3. Navigate to: `Tunneling26-27/STM32H745_Nucleo_M7_ETH`
4. Select the project folder and click **Finish**

### 2. Project Structure

The project contains:
- **CM7** folder: Cortex-M7 core code (main application)
- **CM4** folder: Cortex-M4 core code (if using dual-core)
- **Core/Src/main.c**: Main application code with TCP server and LED control

### 3. Verify Configuration

The project is pre-configured with:
- **Board IP**: `192.168.1.10` (static)
- **Port**: `8080` (TCP)
- **LwIP stack**: Enabled with FreeRTOS
- **Ethernet**: RMII interface configured

### 4. Build the Project

1. **Right-click** on the project in Project Explorer
2. Select **Build Project** (or press `Ctrl+B`)
3. Wait for the build to complete
4. Check the **Console** for any errors

**Expected Output**:
```
Finished building target: STM32H745_Nucleo_M7_ETH.elf
```

### 5. Flash to Board

1. **Connect** the board via USB to your computer
2. Ensure the board is powered (LED should be on)
3. **Right-click** on the project
4. Select **Run As** → **STM32 MCU C/C++ Application**
5. Wait for the flashing process to complete

**Alternative Method**:
- Click the **Run** button (green play icon) in the toolbar
- Or press `Ctrl+F11`

**Expected Output**:
```
Info : stm32h7x.cpu0: hardware has 8 breakpoints, 4 watchpoints
Info : Listening on port 3333 for gdb connections
target halted due to debug-request, current mode: Thread
xPSR: 0x01000000 pc: 0x08000200 msp: 0x20020000
Info : device id = 0x10016453
Info : flash size = 2048kbytes
...
```

### 6. Verify Board Operation

During the flash, you should see:
- LED4 flashes green and red in quick succession.
- **Board is ready** when the flashing stops and the LED stays red

After the flash is complete: 
- Yellow user LED will flash a few times. 
- unplugging and replugging the power will cause the Yellow LED to flash a few times as well.

---

## Running the Python Communication Script

### Prerequisites

The Python script (`send_messages.py`) requires:
- Python 3.x installed
- Standard library modules (socket, threading, etc.)
- On Windows: `msvcrt` (built-in)

### Usage

1. **Open Terminal/Command Prompt**:
   - Navigate to the project directory:
     ```bash
     cd Tunneling26-27
     ```

2. **Run the Script**:
   ```bash
   python send_messages.py
   ```

3. **Connect to Board**:
   - The script will automatically connect to `192.168.1.10:8080`
   - You should see: `Connected successfully!`

4. **Use the Interface**:
   - **LED Commands**: Press `g`, `r`, or `y` to toggle LEDs immediately (no Enter needed)
   - **Send Messages**: Type any text and press Enter to send
   - **Receive Messages**: Incoming "ping" messages and echoes will be displayed automatically
   - **Quit**: Type `quit` or `exit` and press Enter

### Example Session

```
Connecting to STM32 board at 192.168.1.10:8080...
Connected successfully!

LED commands: Press 'g', 'r', or 'y' to toggle LEDs (sent immediately)
Other messages: Type and press Enter to send.
Type 'quit' or 'exit' to disconnect.

You: g
[14:23:15] >>> Sent LED command: g
[14:23:16] <<< Received: ping
You: hello
[14:23:20] >>> Sent: hello
[14:23:20] <<< Received: hello
[14:23:21] <<< Received: ping
You: r
[14:23:25] >>> Sent LED command: r
[14:23:26] <<< Received: ping
You: quit
Disconnecting...
Connection closed. Total messages sent: 3
```

---

## Troubleshooting

### Connection Issues

**Problem**: Cannot connect to board
- **Check**: Ethernet cable is connected
- **Check**: Laptop IP is `192.168.1.1` (run `ipconfig` or `ifconfig`)
- **Check**: Board is powered and flashed correctly
- **Check**: Firewall isn't blocking port 8080

**Problem**: "Connection timeout" error
- **Solution**: Verify board IP is `192.168.1.10` in the code
- **Solution**: Check Ethernet adapter is active (not disabled)
- **Solution**: Try disconnecting and reconnecting the Ethernet cable

### Build Issues

**Problem**: Build errors in STM32CubeIDE
- **Solution**: Ensure STM32CubeIDE 2.0+ is installed
- **Solution**: Check that STM32CubeH7 package is installed
- **Solution**: Clean project: **Project** → **Clean...**

**Problem**: Flash fails
- **Solution**: Check USB cable connection
- **Solution**: Verify ST-Link driver is installed
- **Solution**: Try resetting the board (press reset button)

### LED Not Responding

**Problem**: LEDs don't toggle when sending commands
- **Check**: Connection is established (you should see "ping" messages)
- **Check**: Commands are being sent (check script output)
- **Check**: Board code was flashed correctly

### Python Script Issues

**Problem**: Script doesn't capture keystrokes (Windows)
- **Solution**: Run script in Command Prompt (not PowerShell or Git Bash)
- **Solution**: Ensure Python is running in a terminal window

**Problem**: "Module not found" errors
- **Solution**: All required modules are built-in to Python 3.x
- **Solution**: Verify Python 3.x is installed: `python --version`

---

## Network Configuration Summary

| Device | IP Address | Subnet Mask | Port |
|--------|------------|-------------|------|
| **Laptop** | 192.168.1.1 | 255.255.255.0 | - |
| **STM32 Board** | 192.168.1.10 | 255.255.255.0 | 8080 |

**Important**: Both devices must be on the same subnet (`192.168.1.x`) for communication to work.

---

## Project Files

- **`send_messages.py`**: Python script for laptop communication
- **`STM32H745_Nucleo_M7_ETH/`**: STM32CubeIDE project folder
  - **`CM7/Core/Src/main.c`**: Main application code
  - **`CM7/LWIP/`**: LwIP stack configuration
  - **`CM7/Inc/`**: Header files

---

## Additional Notes

- The board sends "ping" messages every 1 second automatically
- All messages sent to the board are echoed back
- LED commands (`g`, `r`, `y`) are case-insensitive
- Multiple clients can connect simultaneously
- The board maintains connection state for each client

---

## License

This project is based on STM32CubeH7 examples and follows the same licensing terms.

---

## Support

For issues related to:
- **STM32CubeIDE**: Check [ST Community](https://community.st.com/)
- **Hardware**: Refer to [STM32H745ZI-Q documentation](https://www.st.com/en/evaluation-tools/nucleo-h745zi-q.html)
- **LwIP**: See [LwIP documentation](https://www.nongnu.org/lwip/)
