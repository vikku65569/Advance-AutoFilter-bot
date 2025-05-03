

FROM python:3.10.8-slim-buster

RUN apt update && apt upgrade -y
RUN apt install git -y
RUN apt update && apt upgrade -y && apt install -y git ffmpeg
COPY requirements.txt /requirements.txt

RUN cd /
RUN pip3 install -U pip && pip3 install -U -r requirements.txt
RUN mkdir /Advance-AutoFilter-bot
WORKDIR /Advance-AutoFilter-bot
COPY . /Advance-AutoFilter-bot
CMD ["python", "bot.py"]
