# coding: utf-8

from vicon_core_api import *
c = Client ('localhost')

from shogun_live_api import CaptureServices
setname = CaptureServices(c)

import csv

import tkinter
import unicodedata

root = tkinter.Tk()
root.geometry('300x300')
root.configure(bg='gray94')
root.title('CSV')

global count
count = 0
file = '20260716.csv'#######################<<<<<<here!!!!!!!!!!!!!
with open(file) as f:
    reader = csv.reader(f)
    t = [row for row in reader]
    count = sum(1 for row in csv.reader(file))

n=0

def get_east_asian_width_count(text):
    S = "True"
    for c in text:
        if unicodedata.east_asian_width(c) == "F" or unicodedata.east_asian_width(c) == "A" or unicodedata.east_asian_width(c) == "W" or c == " ":
            S = "False"
            break
    return S

def abc(num):
    tex = t[num][0]
    setname.set_capture_name(tex)

def getname(evevt):
    global n
    canvas = tkinter.Canvas(root, width=300, height=100, bg="gray94")
    canvas.place(x=0, y=200)
    label = tkinter.Label(root, text=t[n+1][0])
    label.place(x=10, y=200)
    n = n + 1

def getname2(evevt):
    global n
    canvas = tkinter.Canvas(root, width=300, height=100, bg="gray94")
    canvas.place(x=0, y=200)
    label = tkinter.Label(root, text=t[n-1][0])
    label.place(x=10, y=200)
    n = n - 1

def set(evevt):
    global n
    abc(n)

for x in range(count):
    T = get_east_asian_width_count(t[x][0])
    if T == "False":
        label = tkinter.Label(root, text="Contains characters that cannot be used")
        label.place(x=40, y=140)
        label = tkinter.Label(root, text= t[x][0])
        label.place(x=60, y=170)
        label = tkinter.Label(root, text=":")
        label.place(x=50, y=170)
        label = tkinter.Label(root, text=x)
        label.place(x=40, y=170)
        break

if T == "True":
    Button = tkinter.Button(text='up', width=50)
    Button2 = tkinter.Button(text='down', width=50)
    Button3 = tkinter.Button(text='set', width=50)
    Button.bind("<Button-1>", getname2)
    Button.pack(padx=20 , pady=30)
    Button2.bind("<Button-1>", getname)
    Button2.pack(padx=20, pady=0)
    Button3.bind("<Button-1>", set)
    Button3.pack(padx=20, pady=30)
    label = tkinter.Label(root, text=t[0][0])
    label.place(x=10, y=200)

root.mainloop()
