FROM python:3.9

WORKDIR /app

COPY requirements.txt .

# install dependencies
RUN pip install -r requirements.txt

# copy the content of the local src directory to the working directory
COPY src/ .

# environment variables
ENV \
    PYTHONUNBUFFERED=0

# command to run on container start
CMD [ "python", "./app.py" ]
