# look for serial ports and test them

import serial
import serial.tools.list_ports

ports = serial.tools.list_ports.comports()
print("Available serial ports:")
for i, port in enumerate(ports):
    print(f"{i+1}: {port.device} ({port.description})")
port_num = int(input("Enter the number of the port to test: ")) - 1

def get_next_scan(port, baudrate=9600):
    """
    Get the next scan from the serial port.
    """
    # open the port
    port = serial.Serial(port, baudrate)
    # loop to passively listen to the port (until a carriage return is received)
    keystrokes = []
    while True:
        try: # try to read the port, unless a keyboard interrupt is received or kill signal is sent
            data = port.read().decode("utf-8")
            if data:
                keystrokes.append(data)
                if data == "\r":
                    output = "".join(keystrokes)
                    break
        except KeyboardInterrupt:
            break
    # close the port
    port.close()
    return output

# loop to passively listen to the port
data = get_next_scan(ports[port_num].device)
print(f"Received data: {data}")

    
