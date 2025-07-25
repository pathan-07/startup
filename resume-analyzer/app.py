from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from markupsafe import Markup
import sqlite3
import os
import traceback
import io
import re
from dotenv import load_dotenv
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from resume_parser import extract_text
from analysis import analyze_resume
from xhtml2pdf import pisa
from email.message import EmailMessage
from email.utils import formataddr
import smtplib

# App and config
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "your_super_secret_key")  # change to a secure random string
load_dotenv()

DB_PATH = "resume_ai.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Email function
def send_email(receiver_email, subject, body, attachment=None):
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_PASSWORD")

    msg = EmailMessage()
    msg['From'] = formataddr(("CareerGPT", sender_email))
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.set_content(body)

    if attachment:
        msg.add_attachment(attachment.getvalue(), maintype='application', subtype='pdf', filename="Resume_Report.pdf")

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print("Email error:", e)
        return False

# PDF generator
def generate_pdf(html):
    pdf = io.BytesIO()
    pisa.CreatePDF(io.StringIO(html), dest=pdf, encoding='utf-8')
    pdf.seek(0)
    return pdf

# Formatting functions
def format_for_web(text):
    """Format text for better web display with enhanced structure"""
    if not text:
        return '<div class="analysis-content"><p>No analysis data available.</p></div>'
    
    # First, clean up the text
    text = text.strip()
    
    # Convert markdown-style headers with icons
    text = re.sub(r'\*\*(.*?Analysis.*?)\*\*', r'<h5><i class="fas fa-chart-line"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?Score.*?)\*\*', r'<h5><i class="fas fa-star"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?Skills.*?)\*\*', r'<h5><i class="fas fa-code"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?Experience.*?)\*\*', r'<h5><i class="fas fa-briefcase"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?Recommendation.*?)\*\*', r'<h5><i class="fas fa-lightbulb"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?Summary.*?)\*\*', r'<h5><i class="fas fa-file-alt"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?Missing.*?)\*\*', r'<h5><i class="fas fa-exclamation-triangle"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?Improve.*?)\*\*', r'<h5><i class="fas fa-arrow-up"></i> \1</h5>', text, flags=re.IGNORECASE)
    text = re.sub(r'\*\*(.*?)\*\*', r'<h5><i class="fas fa-info-circle"></i> \1</h5>', text)
    text = re.sub(r'\*(.*?)\*', r'<strong>\1</strong>', text)

    # Convert bullet points and format content
    lines = text.split('\n')
    formatted_lines = []
    in_section = False

    for line in lines:
        line = line.strip()
        if not line:
            if in_section:
                formatted_lines.append('</div>')
                in_section = False
            continue
            
        if line.startswith('<h5>'):
            if in_section:
                formatted_lines.append('</div>')
            formatted_lines.append(line)
            formatted_lines.append('<div class="highlight-box">')
            in_section = True
        elif line.startswith('•') or line.startswith('-') or line.startswith('*'):
            bullet_content = line[1:].strip()
            if 'score' in bullet_content.lower() or 'rating' in bullet_content.lower():
                formatted_lines.append(f'<div class="score-box"><i class="fas fa-trophy"></i> {bullet_content}</div>')
            else:
                formatted_lines.append(f'<div class="info-box">• {bullet_content}</div>')
        elif re.match(r'^\d+\.', line):
            # Numbered list
            formatted_lines.append(f'<div class="info-box">{line}</div>')
        elif 'score:' in line.lower() or 'rating:' in line.lower() or re.search(r'\d+/\d+', line):
            formatted_lines.append(f'<div class="score-box"><i class="fas fa-trophy"></i> {line}</div>')
        else:
            if not in_section:
                formatted_lines.append('<div class="highlight-box">')
                in_section = True
            formatted_lines.append(f'<p>{line}</p>')

    # Close any remaining section
    if in_section:
        formatted_lines.append('</div>')

    result = '<div class="analysis-content">' + '\n'.join(formatted_lines) + '</div>'
    return result

# DB helpers
def get_user(email):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    conn.close()
    return user

def save_history(user_id, job_role, result):
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO analysis_history (user_id, job_role, result) VALUES (?, ?, ?)", 
                    (user_id, job_role, result))
        conn.commit()
    except Exception as e:
        print(f"Error saving history: {e}")
    finally:
        conn.close()

# Routes
@app.route("/")
def index():
    # Redirect to login page if not logged in
    if "user_id" not in session:
        return redirect(url_for('login'))
    # If logged in, go to the analysis page
    return redirect(url_for('analyze_page'))

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = generate_password_hash(request.form["password"])

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("INSERT INTO users (name, email, password) VALUES (?, ?, ?)", (name, email, password))
            conn.commit()
            conn.close()
            flash("Signup successful! Please log in.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            conn.close()
            flash("Email already exists.", "danger")

    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            flash("Login successful!", "success")
            return redirect(url_for("analyze_page"))
        else:
            flash("Invalid email or password.", "danger")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))

@app.route("/analyze", methods=["POST"])
def analyze():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    job_role = request.form.get("job_role")
    if not job_role:
        flash("Please specify a job role.", "warning")
        return redirect(url_for("analyze_page"))

    # Get email address from form
    recipient_email = request.form.get("email")

    if "resume" not in request.files:
        flash("No resume file selected.", "danger")
        return redirect(url_for("analyze_page"))

    file = request.files["resume"]
    if not file or file.filename == "":
        flash("No resume file selected.", "danger")
        return redirect(url_for("analyze_page"))

    try:
        resume_text = extract_text(file)
        result = analyze_resume(resume_text, job_role)
        save_history(session["user_id"], job_role, result)

        # Save latest to session for PDF/email
        session["latest_result"] = result
        session["latest_role"] = job_role

        # If email was provided, send the report immediately
        if recipient_email:
            html = render_template("report.html", result=format_for_web(result), job_role=job_role, now=datetime.now())
            pdf = generate_pdf(html)
            email_sent = send_email(recipient_email, f"Resume Report for {job_role}", "Find attached your analysis result.", attachment=pdf)

            if email_sent:
                flash("Analysis report sent to the provided email successfully!", "success")
            else:
                flash("Failed to send email. Please try again later.", "danger")

        flash("Resume analysis completed successfully!", "success")
        return render_template("index.html", result=format_for_web(result), job_role=job_role)
    except Exception as e:
        flash(f"Error analyzing resume: {str(e)}", "danger")
        return redirect(url_for("analyze_page"))

@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM analysis_history WHERE user_id = ? ORDER BY created_at DESC", (session["user_id"],))
    history = cursor.fetchall()
    conn.close()

    return render_template("dashboard.html", name=session["user_name"], history=history)

@app.route("/download-pdf")
def download_pdf():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if "latest_result" not in session:
        flash("No analysis result available to download.", "warning")
        return redirect(url_for("dashboard"))

    try:
        html = render_template("report.html", result=format_for_web(session["latest_result"]), job_role=session["latest_role"], now=datetime.now())
        pdf = generate_pdf(html)
        return send_file(pdf, as_attachment=True, download_name="Resume_Analysis.pdf", mimetype="application/pdf")
    except Exception as e:
        flash(f"Error generating PDF: {str(e)}", "danger")
        return redirect(url_for("dashboard"))

@app.route("/email-result")
def email_result():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    if "latest_result" not in session:
        flash("No analysis result available to email.", "warning")
        return redirect(url_for("dashboard"))

    try:
        html = render_template("report.html", result=format_for_web(session["latest_result"]), job_role=session["latest_role"], now=datetime.now())
        pdf = generate_pdf(html)
        email_sent = send_email(session["user_email"], f"Resume Report for {session['latest_role']}", "Find attached your analysis result.", attachment=pdf)

        if email_sent:
            flash("Analysis report sent to your email successfully!", "success")
        else:
            flash("Failed to send email. Please try again later.", "danger")

        return redirect(url_for("dashboard"))
    except Exception as e:
        flash(f"Error sending email: {str(e)}", "danger")
        return redirect(url_for("dashboard"))

@app.route("/email-analysis/<int:analysis_id>")
def email_analysis(analysis_id):
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))

    # Get the specific analysis
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM analysis_history WHERE id = ? AND user_id = ?", 
                  (analysis_id, session["user_id"]))
    analysis = cursor.fetchone()
    conn.close()

    if not analysis:
        flash("Analysis not found or you don't have permission to access it.", "danger")
        return redirect(url_for("dashboard"))

    try:
        html = render_template("report.html", 
                              result=format_for_web(analysis["result"]), 
                              job_role=analysis["job_role"], 
                              now=datetime.now())
        pdf = generate_pdf(html)
        email_sent = send_email(session["user_email"], 
                               f"Resume Report for {analysis['job_role']}", 
                               "Find attached your analysis result.", 
                               attachment=pdf)

        if email_sent:
            flash("Analysis report sent to your email successfully!", "success")
        else:
            flash("Failed to send email. Please try again later.", "danger")

        return redirect(url_for("dashboard"))
    except Exception as e:
        flash(f"Error sending email: {str(e)}", "danger")
        return redirect(url_for("dashboard"))

@app.route("/analyze-page")
def analyze_page():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))
    return render_template("index.html")

@app.route("/build-resume", methods=["GET", "POST"])
def build_resume():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))
    
    if request.method == "GET":
        return render_template("build_resume.html")
    
    if request.method == "POST":
        # Get form data
        resume_data = {
            'name': request.form.get('name', ''),
            'email': request.form.get('email', ''),
            'phone': request.form.get('phone', ''),
            'job_role': request.form.get('job_role', ''),
            'skills': request.form.get('skills', '').split(',') if request.form.get('skills') else [],
            'education': request.form.get('education', ''),
            'experience': request.form.get('experience', ''),
            'certifications': request.form.get('certifications', ''),
            'achievements': request.form.get('achievements', '')
        }
        
        # Clean up skills list
        resume_data['skills'] = [skill.strip() for skill in resume_data['skills'] if skill.strip()]
        
        # Store in session for PDF generation
        session['generated_resume'] = resume_data
        
        return render_template("resume_result.html", resume_data=resume_data)

@app.route("/download-resume-pdf")
def download_resume_pdf():
    if "user_id" not in session:
        flash("Please log in first.", "warning")
        return redirect(url_for("login"))
    
    if "generated_resume" not in session:
        flash("No resume data available to download.", "warning")
        return redirect(url_for("build_resume"))
    
    try:
        from weasyprint import HTML, CSS
        from weasyprint.text.fonts import FontConfiguration
        
        # Generate HTML for PDF
        html_content = render_template("resume_pdf.html", resume_data=session["generated_resume"])
        
        # Create PDF
        font_config = FontConfiguration()
        html = HTML(string=html_content)
        css = CSS(string="""
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
            body { font-family: 'Inter', sans-serif; margin: 0; padding: 20px; }
            .resume-container { max-width: 800px; margin: 0 auto; }
            .header { background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; padding: 2rem; border-radius: 16px; margin-bottom: 2rem; }
            .section { background: white; padding: 1.5rem; margin-bottom: 1rem; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
            .section h3 { color: #6366f1; font-weight: 600; margin-bottom: 1rem; }
            .skills-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 0.5rem; }
            .skill-tag { background: #f0f9ff; color: #0369a1; padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.875rem; text-align: center; }
        """, font_config=font_config)
        
        pdf = html.write_pdf(stylesheets=[css])
        
        pdf_io = io.BytesIO(pdf)
        pdf_io.seek(0)
        
        return send_file(
            pdf_io,
            as_attachment=True,
            download_name=f"{session['generated_resume'].get('name', 'Resume').replace(' ', '_')}_Resume.pdf",
            mimetype="application/pdf"
        )
        
    except ImportError:
        flash("WeasyPrint is not installed. Please install it to generate PDFs.", "danger")
        return redirect(url_for("build_resume"))
    except Exception as e:
        flash(f"Error generating PDF: {str(e)}", "danger")
        return redirect(url_for("build_resume"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)