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
from flask_migrate import Migrate
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()

# Configuración de la aplicación usando variables de entorno
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('SQLALCHEMY_DATABASE_URI')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
# Cache configuration
app.config['CACHE_TYPE'] = 'simple'

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
# Configuración de OAuth usando variabdles de entorno
app.config['GOOGLE_CLIENT_ID'] = os.getenv('GOOGLE_CLIENT_ID')
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv('GOOGLE_CLIENT_SECRET')
app.config['PREFERRED_URL_SCHEME'] = 'https'

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    authorize_params=None,
    access_token_url='https://accounts.google.com/o/oauth2/token',
    access_token_params=None,
    refresh_token_url=None,
    redirect_uri='https://itsenem.com/callback',
    client_kwargs={'scope': 'email profile'},
)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Código para crear las tablas en el contexto de la aplicación
with app.app_context():
    db.create_all()

# Token de API como variable de entorno
os.environ["DEEPINFRA_API_TOKEN"] = os.getenv("DEEPINFRA_API_TOKEN")

# Configuración de Stripe usando variables de entorno
import stripe
stripe.api_key = os.getenv('STRIPE_API_KEY')

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

@app.route('/login')
def login_google():
    redirect_uri = url_for('callback', _external=True, _scheme='https')
    print("Redirigiendo")
    print(redirect_uri)
    return google.authorize_redirect(redirect_uri)



@app.route('/callback')
def callback():
    token = google.authorize_access_token()
    session['google_token'] = token
    user_info = google.get('https://www.googleapis.com/oauth2/v1/userinfo').json()

    user = User.query.filter_by(email=user_info['email']).first()
    if not user:
        # Si el usuario no existe, verifica si el nombre de usuario está tomado
        existing_user = User.query.filter_by(username=user_info['name']).first()
        if existing_user:
            # Si el nombre de usuario ya existe, modifica el nombre de usuario
            base_username = user_info['name']
            counter = 1
            new_username = f"{base_username}{counter}"
            while User.query.filter_by(username=new_username).first():
                counter += 1
                new_username = f"{base_username}{counter}"
            user_info['name'] = new_username

        user = User(
            username=user_info['name'], 
            email=user_info['email'], 
            google_id=user_info['id'],
            subscription_type='free'  # Inicializar con 'free' u otro valor según tus necesidades
        )
        db.session.add(user)
        db.session.commit()

    login_user(user)
    flash('Autenticación exitosa. ¡Bienvenido!', 'success')
    return redirect(url_for('app_index'))


@app.route('/subscribe')
@login_required
def subscribe():
    if current_user.subscription_type == 'paid':
        flash('Ya tienes una suscripción activa.', 'info')
        return redirect(url_for('index'))
    
    # Verificar si el usuario ya ha usado un trial o si su suscripción está pausada
    if has_used_trial(current_user.stripe_customer_id):
        # Redirigir a un enlace de pago sin trial
        payment_link = "https://buy.stripe.com/4gw0417Po5bTeCQaEG"  # Enlace de pago sin trial
        flash("Ya has utilizado tu período de prueba o tu suscripción está pausada. Puedes suscribirte con un plan pago.", 'info')
    else:
        # Redirigir a un enlace de pago con trial
        payment_link = "https://buy.stripe.com/4gwaIF3z8cElbqE3cc"  # Enlace de pago con trial
    
    return redirect(payment_link)


def has_used_trial(stripe_customer_id):
    """
    Función para verificar si un usuario ha utilizado un trial basado en el historial de suscripciones en Stripe.
    Devuelve True si el usuario ya ha usado un trial o si la suscripción está pausada.
    """
    if not stripe_customer_id:
        return False  # El usuario no tiene un stripe_customer_id, es un nuevo usuario

    # Obtener suscripciones previas del cliente en Stripe
    subscriptions = stripe.Subscription.list(customer=stripe_customer_id)

    # Si no hay suscripciones, el usuario no ha usado un trial
    if not subscriptions['data']:
        return False  # Usuario nuevo, puede usar el trial

    # Verificar si alguna suscripción anterior tenía un trial o está pausada
    for sub in subscriptions['data']:
        if sub.trial_end and sub.status in ['trialing', 'active', 'past_due', 'paused', 'canceled']:
            return True  # Ya ha usado un trial o la suscripción está pausada

    return False  # No ha usado un trial previamente ni tiene la suscripción pausada



@app.route('/', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_ENDPOINT_SECRET')  # Usa una variable de entorno para el secreto del webhook

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except ValueError as e:
        # Invalid payload
        return jsonify({'error': str(e)}), 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return jsonify({'error': str(e)}), 400

    # Handle the event types
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_session(session)
    elif event['type'] == 'customer.subscription.created' or event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        handle_subscription_update(subscription)
    elif event['type'] == 'customer.subscription.deleted':
        subscription = event['data']['object']
        handle_subscription_cancellation(subscription)
    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        handle_payment_failed(invoice)
    elif event['type'] == 'customer.subscription.updated':
        subscription = event['data']['object']
        handle_subscription_update(subscription)

    return '', 200


def handle_checkout_session(session):
    customer_email = session.get('customer_details', {}).get('email')
    stripe_customer_id = session.get('customer')  # Obtener el Stripe customer ID
    user = User.query.filter_by(email=customer_email).first()
    
    if user:
        user.subscription_type = 'paid'
        user.subscription_start = datetime.now(timezone.utc)
        user.stripe_subscription_id = session.get('subscription')
        user.stripe_customer_id = stripe_customer_id  # Guardar el customer_id en la base de datos
        db.session.commit()


def handle_subscription_cancellation(subscription):
    user = User.query.filter_by(stripe_subscription_id=subscription.id).first()
    if user:
        user.subscription_type = 'free'
        user.stripe_subscription_id = None
        db.session.commit()

def handle_payment_failed(invoice):
    # Obtener el ID de cliente y buscar al usuario
    customer_id = invoice['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    
    if user:
        # Actualizar la suscripción del usuario a 'free' o notificar sobre el fallo de pago
        user.subscription_type = 'free'
        db.session.commit()
        # Aquí puedes enviar una notificación al usuario si lo deseas


def handle_subscription_update(subscription):
    customer_id = subscription['customer']
    user = User.query.filter_by(stripe_customer_id=customer_id).first()
    
    if user:
        # Evitar sobrescribir el estado si ya está marcado para cancelarse
        if user.subscription_type == 'canceled_pending':
            return
        
        if subscription['status'] == 'trialing':
            user.subscription_type = 'trial'
        elif subscription['status'] == 'active':
            user.subscription_type = 'paid'
        elif subscription['status'] == 'past_due':
            user.subscription_type = 'past_due'
        elif subscription['status'] == 'canceled':
            user.subscription_type = 'canceled'
        elif subscription['status'] == 'paused':
            user.subscription_type = 'paused'

        user.stripe_customer_id = customer_id
        db.session.commit()



@app.route('/cancel_subscription', methods=['POST'])
@login_required
def cancel_subscription():
    user = current_user
    if user.stripe_subscription_id:
        try:
            response = stripe.Subscription.modify(
                user.stripe_subscription_id,
                cancel_at_period_end=True
            )
            print(f"Stripe modify response: {response}")
            user.subscription_type = 'canceled_pending'
            db.session.commit()
            print(f"Subscription marked as canceled_pending for user: {user.email}")
        except stripe.error.StripeError as e:
            print(f"Error during subscription cancellation: {e}")
    else:
        print("No active subscription found to cancel.")

    return redirect(url_for('app_index'))



@app.route('/select_exam', methods=['POST'])
@login_required
def select_exam():
    if current_user.subscription_type not in ['trial', 'paid']:
        flash('Necesitas una suscripción activa para acceder a los exámenes.', 'warning')
        return redirect(url_for('app_index'))
    exam_type = request.form.get('exam_type')
    if not exam_type:
        return "No se ha seleccionado ningún examen", 400
    
    return render_template('speciality.html', exam_type=exam_type)

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
                # Genera preguntas usando la función existente
                questions = generate_questions(chat, context, num_items - questions_generated)
                
                # Validar preguntas generadas
                valid_questions = [q for q in questions if validate_question(q)]
                
                # Agregar preguntas válidas a los resultados
                results.extend(valid_questions)
                questions_generated = len(results)
                
                print(f"Preguntas válidas generadas hasta ahora: {questions_generated} de {num_items}")

                # Si no se alcanzó el número requerido de preguntas, incrementar reintentos
                if questions_generated < num_items:
                    print(f"No se generaron suficientes preguntas válidas. Reintento {reintentos + 1}...")
                    reintentos += 1

            except Exception as e:
                print(f"Error al generar preguntas: {str(e)}")
                reintentos += 1

        if questions_generated < num_items:
            print(f"Advertencia: No se pudieron generar todas las preguntas válidas. Se generaron {questions_generated} de {num_items}.")

        # Incrementa el contador de preguntas para el usuario actual
        current_user.increment_questions()
        db.session.commit()  # Asegúrate de guardar los cambios en la base de datos

        return render_template('quiz.html', questions=results)

# Función para validar preguntas
def validate_question(question):
    """
    Verifica que la pregunta tenga los campos necesarios y estén correctamente formateados.
    """
    if not question:
        return False
    if 'question' not in question or not question['question']:
        return False
    if 'choices' not in question or not question['choices']:
        return False
    if len(question['choices']) < 2:  # Asegurar que haya al menos dos opciones
        return False
    return True


@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json['message']
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3.1-8B-Instruct", max_tokens=4000)
    system_text = "Eres un asistente de examen que proporciona respuestas generales a preguntas relacionadas con el examen. Responde en brasileño"
    human_text = user_message
    prompt = ChatPromptTemplate.from_messages([("system", system_text), ("human", human_text)])
    
    response = prompt | chat
    response_msg = response.invoke({})
    response_text = response_msg.content
    
    return jsonify({"response": response_text})


@app.route('/check', methods=['POST'])
def check():
    data = request.get_json()
    print("Datos recibidos del frontend:", data)  # Imprimir los datos recibidos

    if not data:
        print("Error: No se recibieron datos.")
        return jsonify({"error": "No se recibieron datos"}), 400
    
    questions = data.get('questions')
    user_answers = data.get('answers')

    if not questions or not user_answers:
        print("Error: Faltan preguntas o respuestas.")
        return jsonify({"error": "Faltan preguntas o respuestas"}), 400

    # Inicializar el chat con el modelo
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3.1-8B-Instruct", max_tokens=4000)
    results = []

    for i, question in enumerate(questions):
        question_name = f'question_{i+1}'
        user_answer = user_answers.get(question_name)
        
        print(f"Procesando {question_name}: respuesta seleccionada = {user_answer}")  # Imprimir respuesta seleccionada
        
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
            # Usar siempre check_answer para verificar la respuesta
            correctness, explanation = check_answer(question, user_answer, chat)
            
            print(f"Resultado de {question_name}: correcto = {correctness}, explicación = {explanation}")  # Imprimir resultados

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


# Webhook route to handle Stripe events
import stripe

@app.route('/', methods=['POST'])
def webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = 'whsec_xpqBGgt4EGordrpUfEvwR3OFOgSgKIFm'  # Asegúrate de que esta sea la clave secreta correcta

    print("Payload recibido:", payload)  # Imprimir el payload recibido
    print("Cabecera de firma recibida:", sig_header)  # Imprimir la cabecera de firma recibida

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
        print("Firma validada exitosamente.")
    except ValueError as e:
        # Payload inválido
        print("Error: Payload inválido:", e)
        return '', 400
    except stripe.error.SignatureVerificationError as e:
        # Firma inválida
        print("Error de verificación de firma:", e)
        print("Cabecera de firma esperada:", endpoint_secret)  # Imprimir la clave de firma esperada para comparación
        return '', 400

    # Manejar el evento (por ejemplo, un pago completado)
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        handle_checkout_session(session)

    return '', 200

@app.route('/charge', methods=['POST'])
def charge():
    # `stripeToken` is obtained from the form submission
    token = request.form['stripeToken']

    try:
        # Use Stripe's library to make requests...
        charge = stripe.Charge.create(
            amount=2000,  # $20.00
            currency='usd',
            description='Example charge',
            source=token,
        )
        return render_template('success.html', amount=20)
    except stripe.error.StripeError as e:
        # Handle error
        return str(e)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8001) 