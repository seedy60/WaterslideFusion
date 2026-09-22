# Waterslide Fusion

Speed your way down nine waterslides complete with twists, turns, loop-the-loops, and evil ducks and crabs that will get in your way with every chance they get.

## Table of Contents
- [1. Introduction](#1-introduction)
- [2. Setup](#2-setup)
- [3. The Game](#3-the-game)
- [4. Controls](#4-controls)
- [5. Speech and Audio](#5-speech-and-audio)
- [6. Options](#6-options)
- [7. The Tutorial](#7-the-tutorial)

## 1. Introduction

Waterslide Fusion is a Windows port of Waterslide Extreme, the July 2009 iOS waterslide racer developed by Fishlabs and sponsored by Barclaycard, rebuilt in Python and designed to be fully playable by the blind. It uses the original game's level files and sound effects, with every menu and gameplay event narrated.

Waterslide Extreme was the very first mobile game I ever played. It was installed on my late father's iPhone and I absolutely loved playing it back in 2009 and 2010. I got an iPhone 5S of my own in 2013 and downloaded the game as soon as I got my Apple ID the following year. Though the iPhone 5s has a 64-bit processor, this was back when Apple still allowed 32-bit apps to run. Then iOS 11 happened in 2017, 32-bit support was killed, and because Waterslide Extreme was never updated, it stopped working and was wiped from the app store.

My dad died on January 7th 2026, so reviving this game is an act of preserving his memory as well as preserving the game itself.

There are three ways to experience the game:

- Nine race stages with diamonds, boost pads, obstacles and loop-the-loops.
- The audio tutorial, which lets you learn the game and its rules before you ride.
- A 3D window over the slide that follows the OS dark mode setting (a theme override lives in Options) and stands down to flat black and white when a high contrast scheme is active. Item markers use a colourblind-safe palette, and every item class has its own 3D shape so markers stay identifiable without any colour vision.

### 1.1. Design principles

1. Everything is spoken. Menus, items ahead, falls and results are announced through your screen reader, detected automatically at launch, or through the built-in engines.
2. Nothing requires the mouse. The game is keyboard driven; a click only dismisses the results and death dialogs. The original game involved lightly tilting your phone left and right rather than swiping, so using the mouse wouldn't make sense anyway.
3. Sound has position. Items pan left and right in stereo and are described with clock-face positions, so you can hear where they are.

### 1.2. Versioning

This game is 2009 through and through. To further add to the retro vibe, the game uses a 2009.x.x version scheme.

## 2. Setup

### 2.1 Running from source

1. Press Windows + R, type powershell and press Enter.
2. Install UV if it isn't already installed.

    ```PowerShell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

3. Clone the repository and cd into its directory.

    ```sh
    git clone https://github.com/seedy60/WaterslideFusion
    cd WaterslideFusion
    ```

4. Install required libraries.

    ```sh
    uv sync
    ```

5. Run the game.

    ```sh
    uv run python run_game.py
    ```

### 2.2 Running compiled

1. [Download the latest release binary](https://github.com/seedy60/WaterslideFusion/releases/latest/download/waterslide.zip).
2. Extract the zip file to a folder of your choice.
3. Run WaterslideFusion.exe.

The game speaks through whichever engine is available: Prism on Windows 10 and 11 (NVDA, JAWS, SAPI, OneCore and more), accessible_output2 on Windows 8.1 and earlier; Apple killed WSE the moment they stopped supporting older generations, and the game could very well have become lost media if it wasn't this project. To paraphrase George Santayana, those who don't learn from history are doomed to repeat it.

Speech is set to auto detect by default: the game checks whether a screen reader is actually running and speaks only when it finds one. Sighted players who want speech can enable it at any time with Shift + V.

## 3. The Game

Fly down the tube, collect diamonds and boost pads, and avoid crabs and ducks that will slow you down if you hit them. You have three lives. Riding too high up the wall for too long tips you over the edge, which costs a life, and at zero lives the stage ends in defeat.

The god mode star makes you invincible for eight seconds: obstacles are squashed for points instead of slowing you down, and riding the wall cannot tip you out of the slide while it lasts. 

Completing or losing a stage opens a results dialog with your time, the diamonds you collected, your remaining lives and your final score. Press Enter, or click, to dismiss it; the menu underneath then offers next stage, replay stage, stage select and main menu. R repeats the results at any time. Finishing a stage unlocks the next one, and scores are recorded under your player name, set from the main menu.

Diamonds are worth one hundred points each, so the maximum number of points you can get from diamonds alone on a sixty-five-diamond slide is six thousand five hundred.

## 4. Controls

Menus: arrows navigate, Enter or Space selects, Escape goes back.

| Key | Action |
| :--- | :--- |
| Left / Right (or A / D) | Steer toward the walls |
| Down (or S) | Brake |
| Space | Fire a boost pad to gain more speed |
| P | Progress report: stage, percent complete, speed |
| C | Score |
| I | Diamonds collected so far |
| L | Lives remaining |
| R | Radar: the next items with clock positions and metres |
| E | Scan: the next 15 seconds of slide |
| M | Cycle the speech mode: auto detect, always on, off |
| V | Toggle the 3D view |
| Shift + V | Turn speech on or off |
| Page Up / Page Down | Music volume |
| Shift + Page Up / Page Down | Sound effects volume |
| Home / End | Music muted / full volume |
| Shift + Home / End | Sound effects muted / full volume |
| Enter or click | Dismiss the results or death dialog |
| Escape | Pause (resume, restart, main menu) |

## 5. Speech and Audio

The game  doesn't require a screen reader to play, though by default it pipes through a screen reader if one is running. Prism is the default engine on Windows 10 and 11 and offers engine, output, voice, rate, volume, pitch and braille settings. Accessible_output2 covers Windows 8.1 and earlier. All of it is configured in Options and saved.

The classic Waterslide Extreme background music is on by default and loops seamlessly. Water and wind ambience, synthesized in real time, pans with your position and changes volume with your speed; the louder the sound, the faster you're moving. Every pickup and obstacle plays panned to where it sits on the tube wall.

## 6. Options

Use up and down arrows to navigate to an option, left and right to adjust it. You can change the speech engine and output backend, the volume of the music and sound effects, the character voice (male or female), speech volume, rate and pitch, speed units (miles  per hour or kilometres per hour), and more.

## 7. The Tutorial

The audio tutorial, second item on the main menu, allows you to learn the mechanics of the game and practice before playing. In this mode, the music is silent, the slide is not moving and you can't fall out. If you get stuck, you can repeat the last instruction by pressing R.

## Legal and credits

The original 2009 Waterslide Extreme IPA was disassembled and analyzed to interpret the game's rules and mechanics and preserve the original sounds and assets. Not everything could be extracted since the IPA is encrypted with Apple's FairPlay DRM, and no attempt was made to try and defeat this DRM, but there were many plain text dumps of the menus, game items and other elements that could be found, which helped a lot with development.

Waterslide Extreme is copyright 2009 Fishlabs, sponsored by Barclaycard. All visuals, sounds and other assets are properties of their respective owners. No financial gain shall be obtained from this rewrite, be it through commercial sale, advertisements, merchandising or any other means.