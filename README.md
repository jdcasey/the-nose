# The Nose - Indoor Air Quality Monitoring Project

This project is an attempt to measure the environment where I work daily. My hope is to gain a better understanding of different environmental health factors, especially things that make my sinuses hate me.

## What Can I Monitor?

At this point, I'm able to monitor the following levels:

* Total VOC (ppb)
* Estimated CO₂ (ppm)
* PM100 concentration index (standard & environmental)
* 10μm particles per 0.1L of air volume
* 5μm particles per 0.1L of air volume
* 2.5μm particles per 0.1L of air volume
* 1.0μm particles per 0.1L of air volume
* 0.5μm particles per 0.1L of air volume
* 0.3μm particles per 0.1L of air volume

## Components

### Hardware

Currently, this project consists of the following hardware:

* Raspberry Pi Zero W
* [Sparkfun QWIIC shim for pi](https://www.adafruit.com/product/4463)
* [Adafruit PMSA003I Air Quality Breakout](https://www.adafruit.com/product/4632)
* [Adafruit SGP30 Air Quality Sensor Breakout - VOC and eCO2](https://www.adafruit.com/product/3709)
* [Adafruit 50mm QWIIC cable](https://www.adafruit.com/product/4399) (x2)
* 5V@2A USB micro B power supply

### Software

Along with this hardware, I've written a simple Python script to perform the measurements and log them to [Honeycomb.io](https://honeycomb.io/). I've also written a Systemd boot service for the script, and Ansible automation for installing and updating the system.

## Raspberry Pi Prerequisites

You will need a Raspberry Pi with a Debian-variant Linux install that has access to Python 3. This Pi will need a Wi-Fi connection (or ethernet, if you're using a different configuration), and SSH must be enabled.

Additionally, you'll need to enable the I²C in the Pi Peripherals menu under `sudo raspi-config`. After enabling that, you might want to reboot to be safe.

As always, you should also update your operating system to improve its security safety. You can do this with Debian using `sudo apt-get update && sudo apt-get upgrade`.

Since we're using SSH to access the Pi, you should also change the password for your `pi` user.

You can do even more to protect your little Pi, such as installing `fail2ban`, but that's beyond the scope of this project. I highly encourage you to Google it.


## Installing via Ansible

If you have Ansible, you can use the playbook and directory setup in this project, along with an inventory you configure for your environment, to automatically install The Nose. This is attractive, since it removes a lot of the guesswork. It's also nice if you decide to set up multiple nodes, and you want to update them en masse.

### Setting Up Your Inventory

You can copy the inventory from `examples/inventory`, or even change it in place. You'll need to set the IP address and write key of the host `nose1` at a minimum. You can do that by changing `examples/inventory/host_vars/nose1.yml`. 

### Running the Ansible Install

When you have your inventory setup, you can run (from this project root):

```bash
$ ansible-playbook -i /path/to/your/inventory/hosts ./install.yml
[LOTS of output]
```

When it comes to Ansible, green either means "good" or "no change necessary", yellow / brown means "changed", and as always, red is bad. When this completes, you should have an installation on your Pi that will start when the machine boots. It can take a few seconds for the script to start...it seems that Systemd can be a tad slow at times.

## Python Script (including manual installation)

This project mainly centers on a short Python script that uses the Raspberry Pi I²C bus to read data from two sensors. It's fairly straightforward, especially with the use of Adafruit's Python libraries, which are written to work with the sensors. Along with reading the sensor data, the script also initializes a connection to Honeycomb, then creates and sends events for each measurement pass. The events contain fields with the sensor data, so you can see all the data for a given timestamp.

The script itself is currently designed to be installed in `/home/pi/thenose`, and use a `venv` virtual environment with Python 3, which is under `/home/pi/thenose/venv`. You can initialize this yourself if you like, using:

```bash
$ ssh pi@nose1
$ mkdir /home/pi/thenose
$ cd /home/pi/thenose
[exit the SSH connection and copy the script files measure.py and requirements.txt to the new directory]
[SSH back into the pi]
$ python3 -m venv venv
$ venv/bin/pip install -r ./requirements.txt
```

It also relies on a configuration YAML file, where environment-specific (and in the case of the Honeycomb write key, sensitive) information is stored separately from the code. This configuration installs into `/home/pi/.config/thenose/config.yaml`. The configuration looks something like this:

```yaml
---
write_key: aabbccddeeff00112233445566778899
dataset: air-quality
sample_frequency: 60
node_id: nose1
co2_baseline: 0x954e
tvoc_baseline: 0x9175
quiet: true

```

Once you have all of this done, you should be able to SSH in and run the script manually:

```bash
$ ssh pi@nose1
$ thenose/venv/bin/python3 thenose/measure.py /home/pi/.config/thenose/config.yaml
```

