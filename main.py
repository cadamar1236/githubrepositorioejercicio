from flask import Flask, render_template, request, jsonify
from exani import generate_questions_exani, check_answer_exani, generate_new_questions_exani
from baccaulareat import generate_solutions_bac, retrieve_documents_bac, extract_relevant_context_bac
from langchain_community.chat_models import ChatDeepInfra
import os
import logging
import datetime
from models import db, User 
import stripe
from elasticsearch import Elasticsearch
from langchain.prompts import ChatPromptTemplate
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
# Ruta inicial: Página principal para seleccionar el tipo de examen
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, User  # Importa db y User desde models.py
import stripe
import os
import logging

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)  # Inicializa db con la aplicación
migrate = Migrate(app, db)  # Configura Flask-Migrate con tu app y db

login_manager = LoginManager()
login_manager.init_app(app)  # Asegúrate de inicializar el LoginManager con la app
login_manager.login_view = 'login'  # Página de inicio de sesión

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Aquí puedes agregar tus rutas y lógica de la aplicación

# Código para crear las tablas en el contexto de la aplicación
with app.app_context():
    db.create_all()

# Token de API
os.environ["DEEPINFRA_API_TOKEN"] = "gtnKXw1ytDsD7DmCSsv2vwdXSW7IBJ5H"



# Set your secret key. Remember to switch to your live secret key in production!
stripe.api_key = 'sk_test_51Pr14b2K3oWETT3EMYe9NiKElssrbGmCHpxdUefcuaXLRkKyya5neMrK4jDzd2qh7GUhYZRQT8wqDaiGB2qtg2Md00fbj6TZqF'


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        exam_type = request.form.get('exam_type')
        if exam_type:
            return redirect('/select_exam')
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        subscription_type = request.form['subscription_type']

        new_user = User(username=username, email=email, subscription_type=subscription_type)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()

        login_user(new_user)
        flash('Te has registrado correctamente', 'success')
        return redirect(url_for('index'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        
        if user is None or not user.check_password(password):
            flash('Usuario o contraseña incorrectos', 'danger')
            return redirect(url_for('login'))

        login_user(user)
        flash('Has iniciado sesión correctamente', 'success')
        return redirect(url_for('index'))

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Has cerrado sesión', 'success')
    return redirect(url_for('index'))

@app.route('/subscribe', methods=['GET', 'POST'])
@login_required
def subscribe():
    if request.method == 'POST':
        subscription_type = request.form['subscription_type']
        
        # Reemplaza estos enlaces con los Payment Links reales de Stripe
        payment_links = {
            'premium': 'https://buy.stripe.com/test_28o8xO2p8aXmeeA8wx',
            'pro': 'https://buy.stripe.com/test_28o8xO2p8aXmeeA8wx',
        }

        if subscription_type not in payment_links:
            flash('Tipo de suscripción inválido', 'danger')
            return redirect(url_for('subscribe'))

        return redirect(payment_links[subscription_type])

    return render_template('subscribe.html')

from flask import render_template, redirect, url_for, flash
from flask_login import current_user, login_required
from datetime import datetime

@app.route('/profile')
@login_required
def profile():
    # Verifica la suscripción actual del usuario
    subscription_type = current_user.subscription_type
    subscription_start = current_user.subscription_start

    # Renderiza una plantilla con la información de la suscripción
    return render_template('profile.html', subscription_type=subscription_type, subscription_start=subscription_start)


@app.route('/payment_success')
@login_required
def payment_success():
    flash('¡Tu pago ha sido exitoso! Tu suscripción ha sido actualizada.', 'success')
    return render_template('success.html')

@app.route('/payment_cancel')
@login_required
def payment_cancel():
    flash('Tu pago ha sido cancelado.', 'danger')
    return render_template('cancel.html')



@app.route('/select_exam', methods=['POST'])
def select_exam():
    exam_type = request.form.get('exam_type')
    if not exam_type:
        return "No se ha seleccionado ningún examen", 400
    # Procesar el tipo de examen
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
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3-8B-Instruct", max_tokens=4000)

    if exam_type == "exani_ii":
        segmento = request.form['segmento']
        asignatura = request.form['asignatura']
        questions = generate_questions_exani(chat, num_items, segmento, asignatura)

        # Incrementa el contador de preguntas para el usuario actual
        current_user.increment_questions()
        db.session.commit()  # Asegúrate de guardar los cambios en la base de datos

        return render_template('quiz.html', questions=questions)

    elif exam_type == "baccalaureat":
        speciality = request.form['speciality']
        es = Elasticsearch(
            cloud_id="d6ad8b393b364990a49e2dd896c25d44:dXMtY2VudHJhbDEuZ2NwLmNsb3VkLmVzLmlvJDEwNGY0NzdmMzJjNTQ3MmU4NDY5NmVlYTMwZDI0YzMzJDk2NTU5M2I5NGUxZDRhMjU5MDVlMTc5MmY0YzczZGI4",
            basic_auth=("elastic", "eUqFwSxXebwNHSEH1Bjq1zbM"))
        relevant_docs = retrieve_documents_bac(es, "general_texts", 20, speciality)
        context = extract_relevant_context_bac(relevant_docs)
        solutions = generate_solutions_bac(chat, context, num_items)
        solutions_as_items = [{'question': solution, 'choices': None} for solution in solutions.split('\n\n')]

        # Incrementa el contador de preguntas para el usuario actual
        current_user.increment_questions()
        db.session.commit()  # Asegúrate de guardar los cambios en la base de datos

        return render_template('solutions.html', solutions=solutions_as_items)




# Ruta para manejar las solicitudes del chat
@app.route('/chat', methods=['POST'])
def chat():
    user_message = request.json['message']
    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3-8B-Instruct", max_tokens=4000)
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
    print("Datos recibidos del frontend:", data)  # Imprimir los datos recibidos

    if not data:
        print("Error: No se recibieron datos.")
        return jsonify({"error": "No se recibieron datos"}), 400
    
    questions = data.get('questions')
    user_answers = data.get('answers')

    if not questions or not user_answers:
        print("Error: Faltan preguntas o respuestas.")
        return jsonify({"error": "Faltan preguntas o respuestas"}), 400

    chat = ChatDeepInfra(model="meta-llama/Meta-Llama-3-8B-Instruct", max_tokens=4000)
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

        correctness, explanation = check_answer_exani(question, user_answer, chat)
        print(f"Resultado de {question_name}: correcto = {correctness}, explicación = {explanation}")  # Imprimir resultados

        results.append({
            'question': question,
            'selected_option': user_answer,
            'correct': correctness,
            'explanation': explanation
        })

    return jsonify(results)



# @app.route('/')
# def index():
#   return render_template('index.html')

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

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    print("Webhook recibido")  # Añade este log
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = 'whsec_1Ggvv6DCyU55YjYbzuUnwKbCCfE0Snlw'
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, endpoint_secret
        )
        print(f"Evento construido: {event['type']}")  # Añade este log
    except ValueError as e:
        print(f"Error de valor: {str(e)}")  # Añade este log
        return '', 400
    except stripe.error.SignatureVerificationError as e:
        print(f"Error de verificación de firma: {str(e)}")  # Añade este log
        return '', 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        print("Llamando a handle_checkout_session")  # Añade este log
        handle_checkout_session(session)

    return '', 200

def handle_checkout_session(session):
    print("Llamando a handle_checkout_session")
    print("Sesión completa: %s", session)

    user_email = session.get('customer_details', {}).get('email')
    print("Correo electrónico del usuario: %s", user_email)

    subscription_id = session.get('subscription')
    print("ID de suscripción: %s", subscription_id)

    if subscription_id:
        try:
            subscription = stripe.Subscription.retrieve(subscription_id)
            subscription_type = subscription['items']['data'][0]['plan']['nickname']
            print("Tipo de suscripción encontrado: %s", subscription_type)

            user = User.query.filter_by(email=user_email).first()
            if user:
                print("Usuario encontrado: %s", user.username)
                user.subscription_type = subscription_type
                user.subscription_start = datetime.utcnow()
                db.session.commit()
                print("Base de datos actualizada con éxito.")
            else:
                print("Usuario no encontrado con el correo electrónico: %s", user_email)
        except Exception as e:
            print("Error al recuperar la suscripción: %s", str(e))
    else:
        print("No se encontró ID de suscripción en la sesión.")


@app.route('/success')
def success():
    return "Payment successful!"

@app.route('/cancel')
def cancel():
    return "Payment canceled!"


# Webhook route to handle Stripe events
@app.route('/webhook', methods=['POST'])
def webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')
    event = None

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, 'whsec_iEQcZb38URJgh3gLtkmkWnRWm2BMA72e'
        )
    except ValueError as e:
        # Invalid payload
        return '', 400
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        return '', 400

    # Handle the event
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
    app.run(host='0.0.0.0', port=5000, debug=True)


