from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, current_user, login_user, logout_user, login_required
from exani import generate_questions_exani, check_answer_exani, generate_new_questions_exani
from baccaulareat import generate_solutions_bac, retrieve_documents_bac, extract_relevant_context_bac
from enem import generate_questions, check_answer, retrieve_documents, extract_relevant_context
from langchain_community.chat_models import ChatDeepInfra
import os
from datetime import datetime, timezone
import logging
from models import db, User
import stripe
from elasticsearch import Elasticsearch
from flask_caching import Cache
from langchain.prompts import ChatPromptTemplate
import re
import uuid
from flask_talisman import Talisman
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from flask_dance.contrib.google import make_google_blueprint, google

app = Flask(__name__)
load_dotenv()

# Configuración de la aplicación usando variables de entorno
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_SECURE'] = True  # Solo enviar cookies a través de HTTPS
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevenir acceso de JavaScript a las cookies de sesión
app.config['CACHE_TYPE'] = 'simple'  # Cache configuration

# Inicialización de extensiones
db.init_app(app)
migrate = Migrate(app, db)
talisman = Talisman(app)
login_manager = LoginManager(app)
login_manager.login_view = 'google.login'  # Cambia esto al nombre del blueprint de Google
cache = Cache(app)

# Configuración de Stripe usando variables de entorno
stripe.api_key = os.getenv('STRIPE_API_KEY')

# Configuración de OAuth con Google usando Flask-Dance
google_bp = make_google_blueprint(
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    redirect_to="google_login"
)
app.register_blueprint(google_bp, url_prefix="/login")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Código para crear las tablas en el contexto de la aplicación
with app.app_context():
    db.create_all()

@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/app')
def app_index():
    if current_user.is_authenticated:
        subscription_type = current_user.subscription_type
        questions_asked = current_user.questions_asked
    else:
        subscription_type = None
        questions_asked = 0

    return render_template('index.html', subscription_type=subscription_type, questions_asked=questions_asked)

@app.route('/login/google')
def google_login():
    if not google.authorized:
        return redirect(url_for("google.login"))
    resp = google.get("/plus/v1/people/me")
    assert resp.ok, resp.text
    user_info = resp.json()
    user = User.query.filter_by(email=user_info["email"]).first()

    if user is None:
        user = User(username=user_info["name"], email=user_info["email"], google_id=user_info["id"])
        db.session.add(user)
        db.session.commit()

    login_user(user)
    return redirect(url_for('app_index'))

@app.route('/logout')
@login_required
def logout():
    logout_user()  # Cierra la sesión del usuario
    flash('Has cerrado sesión', 'success')
    return redirect(url_for('landing'))

@app.route('/subscribe')
@login_required
def subscribe():
    if current_user.subscription_type == 'paid':
        flash('Ya tienes una suscripción activa.', 'info')
        return redirect(url_for('app_index'))

    payment_link = "https://buy.stripe.com/test_00g3dud3M6H66M88wA"  # Tu enlace de pago real de Stripe
    return redirect(payment_link)

@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')  # Use environment variable

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except stripe.error.SignatureVerificationError as e:
        return jsonify({'error': str(e)}), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_session(session)
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_cancellation(subscription)
    
    return '', 200

def handle_checkout_session(session):
    customer_email = session.get('customer_details', {}).get('email')
    user = User.query.filter_by(email=customer_email).first()
    if user:
        user.subscription_type = 'paid'
        user.subscription_start = datetime.now(timezone.utc)
        user.stripe_subscription_id = session.get('subscription')
        db.session.commit()

def handle_subscription_cancellation(subscription):
    user = User.query.filter_by(stripe_subscription_id=subscription.id).first()
    if user:
        user.subscription_type = 'free'
        user.stripe_subscription_id = None
        db.session.commit()

@app.route('/cancel_subscription', methods=['POST'])
@login_required
def cancel_subscription():
    user = current_user
    if user.stripe_subscription_id:
        try:
            stripe.Subscription.delete(user.stripe_subscription_id)
            user.subscription_type = 'free'
            user.stripe_subscription_id = None
            db.session.commit()
            flash('Tu suscripción ha sido cancelada exitosamente. Ahora tienes una cuenta gratuita.', 'success')
        except stripe.error.StripeError as e:
            flash(f'Ocurrió un error al cancelar tu suscripción: {str(e)}', 'danger')
    return redirect(url_for('app_index'))

@app.route('/select_exam', methods=['POST'])
@login_required
def select_exam():
    if current_user.subscription_type != 'paid':
        flash('Necesitas una suscripción activa para acceder a los exámenes.', 'warning')
        return redirect(url_for('app_index'))
    exam_type = request.form.get('exam_type')
    if not exam_type:
        return "No se ha seleccionado ningún examen", 400
    
    return render_template('speciality.html', exam_type=exam_type)

# Asegúrate de que esta función esté en la misma línea
def format_solutions(solutions_text):
    solutions_raw = solutions_text.split("\n\n")
    formatted_solutions = []

    for raw_solution in solutions_raw:
        title_match = re.search(r'^\*\*(.*?)\*\*', raw_solution)
        title = title_match.group(1) if title_match else "Solución"
        text = re.sub(r'^\*\*(.*?)\*\*', '', raw_solution).strip()

        formatted_solutions.append({
            "title": title,
            "text": text,
            "note": None
        })

    return formatted_solutions

@app.route('/generate_exam', methods=['POST'])
def generate_exam():
    exam_type = request.form['exam_type']
    num_items = int(request.form['num_items'])
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3.1-8B-Instruct", max_tokens=4000)
    results = []

    if exam_type == "enem":
        cuaderno_seleccionado = request.form['cuaderno']
        es = Elasticsearch(
            cloud_id="1b04b13a745c44b8931831059d0e3c9c:dXMtY2VudHJhbDEuZ2NwLmNsb3VkLmVzLmlvJDg2M2UyNjljODc0NDQxMjM5OTZhMmE3MDVkYWFmMzkwJDY0MzY1OTA5NzQzYzQyZDJiNTRmZWE1MjI3ZTZmYTc2",
            basic_auth=("elastic", "RV6INIvwks0S1aMR4bSFvLS0")
        )
        relevant_docs = retrieve_documents(es, "general_texts_enempdfs", 20, cuaderno_seleccionado)
        context = extract_relevant_context(relevant_docs)

        reintentos = 0
        max_reintentos = 5  # Límite máximo de reintentos
        questions_generated = 0
        
        while questions_generated < num_items and reintentos < max_reintentos:
            try:
                questions = generate_questions(chat, context, num_items - questions_generated)
                valid_questions = [q for q in questions if validate_question(q)]
                results.extend(valid_questions)
                questions_generated = len(results)
                
                print(f"Preguntas válidas generadas hasta ahora: {questions_generated} de {num_items}")

                if questions_generated < num_items:
                    print(f"No se generaron suficientes preguntas válidas. Reintento {reintentos + 1}...")
                    reintentos += 1

            except Exception as e:
                print(f"Error al generar preguntas: {str(e)}")
                reintentos += 1

        if questions_generated < num_items:
            print(f"Advertencia: No se pudieron generar todas las preguntas válidas. Se generaron {questions_generated} de {num_items}.")

        current_user.increment_questions()
        db.session.commit()

        return render_template('quiz.html', questions=results)

def validate_question(question):
    if not question:
        return False
    if 'question' not in question or not question['question']:
        return False
    if 'choices' not in question or not question['choices']:
        return False
    if len(question['choices']) < 2:
        return False
    return True

@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json['message']
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3.1-8B-Instruct", max_tokens=4000)
    system_text = "Eres un asistente de examen que proporciona respuestas generales a preguntas relacionadas con el examen."
    human_text = user_message
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    
    response = prompt | chat
    response_msg = response.invoke({})
    response_text = response_msg.content
    
    return jsonify({"response": response_text})

@app.route('/check', methods=['POST'])
def check():
    data = request.get_json()
    print("Datos recibidos del frontend:", data)

    if not data:
        print("Error: No se recibieron datos.")
        return jsonify({"error": "No se recibieron datos"}), 400
    
    questions = data.get('questions')
    user_answers = data.get('answers')

    if not questions or not user_answers:
        print("Error: Faltan preguntas o respuestas.")
        return jsonify({"error": "Faltan preguntas o respuestas"}), 400

    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3.1-8B-Instruct", max_tokens=4000)
    results = []

    for i, question in enumerate(questions):
        question_name = f'question_{i+1}'
        user_answer = user_answers.get(question_name)
        
        print(f"Procesando {question_name}: respuesta seleccionada = {user_answer}")
        
        if not user_answer:
            print(f"{question_name} sin respuesta seleccionada.")
            results.append({
                'question': question,
                'selected_option': None,
                'correct': "incorrect",
                'explanation': "No se proporcionó ninguna respuesta"
            })
            continue

        try:
            correctness, explanation = check_answer(question, user_answer, chat)
            
            print(f"Resultado de {question_name}: correcto = {correctness}, explicación = {explanation}")

            results.append({
                'question': question,
                'selected_option': user_answer,
                'correct': correctness,
                'explanation': explanation
            })
        except Exception as e:
            print(f"Error al procesar {question_name}: {str(e)}")
            results.append({
                'question': question,
                'selected_option': user_answer,
                'correct': "error",
                'explanation': f"Error al procesar la respuesta: {str(e)}"
            })

    return jsonify(results)

@app.route('/checkout')
def checkout():
    return render_template('checkout.html')

@app.route('/payment')
def payment():
    return render_template('payment.html')

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {
                        'name': 'Test Product',
                    },
                    'unit_amount': 2000,  # Amount in cents
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=url_for('success', _external=True),
            cancel_url=url_for('cancel', _external=True),
        )
        return jsonify(id=checkout_session.id)
    except Exception as e:
        return jsonify(error=str(e)), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=False)  # Desactiva debug para producción
