# Student Curriculum AI

Student Curriculum AI is an AI-powered academic planning platform built with Django that helps students navigate their degree requirements, monitor academic progress, and make informed decisions about future coursework. By analyzing completed courses and curriculum requirements, the application generates personalized course recommendations and academic guidance using OpenAI.

The platform combines full-stack web development, Firebase Authentication, and artificial intelligence to provide an intelligent advising experience that simplifies curriculum planning and helps students stay on track toward graduation.

---

## Features

### AI-Powered Academic Advising

- Personalized course recommendations based on completed coursework
- AI-generated academic guidance for semester planning
- Intelligent curriculum navigation
- Degree progression analysis using OpenAI
- Personalized recommendations tailored to each student's academic history

### Student Management

- Student registration and authentication
- Secure login with Firebase Authentication
- Student profile management
- Personalized dashboard

### Curriculum Planning

- Track completed courses
- Plan future semesters
- Monitor degree progress
- View curriculum requirements
- Organize academic plans

### Backend Functionality

- Django MVC architecture
- Firebase integration
- Secure authentication
- Database management
- Form validation
- Modular application design

---

## Technology Stack

| Category | Technologies |
|----------|--------------|
| Language | Python |
| Framework | Django |
| Authentication | Firebase Authentication |
| Artificial Intelligence | OpenAI API |
| Database | SQLite |
| Frontend | HTML, CSS, JavaScript |
| Additional Services | Firebase |

---

## How It Works

1. Students create an account using Firebase Authentication.
2. Completed coursework and academic information are stored securely.
3. The application compares completed courses with degree requirements.
4. OpenAI analyzes the student's academic progress.
5. Personalized course recommendations and academic guidance are generated.
6. Students use these recommendations to build future semester schedules and monitor graduation progress.

---

## Installation

### Prerequisites

- Python 3.11+
- Django
- Firebase Project
- OpenAI API Key

### Clone the Repository

```bash
git clone https://github.com/Lennox212/Student-Curriculum-AI.git
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Configure Environment Variables

Create a `.env` file:

```env
OPENAI_API_KEY=your_openai_api_key
FIREBASE_CREDENTIALS=your_firebase_credentials
```

### Run the Application

```bash
python manage.py migrate
python manage.py runserver
```

The application will be available at:

```
http://127.0.0.1:8000
```

---

## Skills Demonstrated

- Python
- Django
- Firebase Authentication
- OpenAI API Integration
- Full-Stack Web Development
- Database Design
- Authentication & Authorization
- AI-Assisted Decision Support
- Software Architecture

---

## Future Enhancements

- Interactive degree progress visualization
- Multi-university curriculum support
- Faculty and advisor portal
- AI chat assistant
- Automatic prerequisite validation
- Exportable academic plans
- Docker deployment
- Unit and integration testing

---

## About

Student Curriculum AI was developed as part of my software engineering portfolio to demonstrate the integration of artificial intelligence into a real-world educational platform. The application combines Django, Firebase, and OpenAI to provide personalized academic recommendations and streamline curriculum planning.

---

## Author

**Lennox Rivera**

Computer Engineering Graduate

Java • Python • Django • Spring Boot • AI Integration • Full-Stack Development
