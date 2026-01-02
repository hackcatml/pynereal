python3.12 -m venv ./venv
source venv/bin/activate

python -m ensurepip --upgrade
python -m pip install --upgrade setuptools

python -m pip install -e ".[all]"
python -m pip install python-dateutil dotenv flask pandas numpy 'uvicorn[standard]' fastapi