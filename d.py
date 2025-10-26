from flask import Flask, redirect, request, render_template, session, flash, url_for
import os
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from werkzeug.utils import secure_filename
from jinja2 import FileSystemLoader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rlcanvas
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from arabic_reshaper import reshape
from bidi.algorithm import get_display

# Initialize Flask app
app = Flask(__name__)

# Redirect homepage to login
@app.route('/')
def home():
    return redirect('/login')

# Example login route (placeholder, your full logic stays in your original file)
@app.route('/login')
def login():
    return "<h1>Login Page</h1><p>This is where your login page would load.</p>"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
