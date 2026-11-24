from uuid import uuid4
from sqlalchemy import String, Integer, Float, ForeignKey, Text, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class Profile(Base):
    __tablename__ = 'profile'
    id: Mapped[str] = mapped_column(String, primary_key=True, default='local_user')
    revision: Mapped[int] = mapped_column(Integer, default=0)
    background: Mapped[str] = mapped_column(Text, default='')
    goal: Mapped[str] = mapped_column(Text, default='')
    language: Mapped[str] = mapped_column(String, default='zh-CN')
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

class Course(Base):
    __tablename__ = 'course'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default='')
    route_version: Mapped[int] = mapped_column(Integer, default=1)

class Concept(Base):
    __tablename__ = 'concept'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    scope: Mapped[str] = mapped_column(Text, default='')
    details: Mapped[dict] = mapped_column(JSON, default=dict)

class CourseConcept(Base):
    __tablename__ = 'course_concept'
    course_id: Mapped[str] = mapped_column(ForeignKey('course.id'), primary_key=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey('concept.id'), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)
    source_chunk_id: Mapped[str | None] = mapped_column(String, nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String, unique=True, nullable=True, default=lambda:str(uuid4()))
    scope: Mapped[str] = mapped_column(Text, default='')

class Prerequisite(Base):
    __tablename__ = 'prerequisite'
    course_id: Mapped[str] = mapped_column(ForeignKey('course.id'), primary_key=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey('concept.id'), primary_key=True)
    prerequisite_id: Mapped[str] = mapped_column(ForeignKey('concept.id'), primary_key=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)

class Material(Base):
    __tablename__ = 'material'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey('course.id'))
    title: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer, default=1)
    sha256: Mapped[str] = mapped_column(String)
    confirmed: Mapped[bool] = mapped_column(default=False)
    pages: Mapped[list] = mapped_column(JSON, default=list)

class SourceChunk(Base):
    __tablename__ = 'source_chunk'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    material_id: Mapped[str] = mapped_column(ForeignKey('material.id'))
    course_id: Mapped[str] = mapped_column(ForeignKey('course.id'))
    material_version: Mapped[int] = mapped_column(Integer)
    page: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String, nullable=True)

class LearnerState(Base):
    __tablename__ = 'learner_state'
    profile_id: Mapped[str] = mapped_column(String, primary_key=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey('concept.id'), primary_key=True)
    mastery_level: Mapped[int] = mapped_column(Integer, default=0)
    mastery_data: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_source: Mapped[str] = mapped_column(String, default='unknown')
    familiarity: Mapped[str] = mapped_column(String, default='unknown')
    support_override: Mapped[str] = mapped_column(String, default='auto')
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    last_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    last_question_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_taught_at: Mapped[str | None] = mapped_column(String, nullable=True)

class LearnerEvent(Base):
    __tablename__ = 'learner_event'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, unique=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey('concept.id'))
    kind: Mapped[str] = mapped_column(String)
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String)

class Lesson(Base):
    __tablename__ = 'lesson'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey('course.id'))
    concept_id: Mapped[str] = mapped_column(ForeignKey('concept.id'))
    version: Mapped[int] = mapped_column(Integer)
    learner_version: Mapped[int] = mapped_column(Integer)
    blocks: Mapped[list] = mapped_column(JSON)
    citations: Mapped[list] = mapped_column(JSON)
    teaching_plan: Mapped[dict] = mapped_column(JSON)

class Turn(Base):
    __tablename__ = 'turn'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, unique=True)
    action: Mapped[str] = mapped_column(String)
    course_id: Mapped[str] = mapped_column(String)
    concept_id: Mapped[str] = mapped_column(String)
    lesson_id: Mapped[str | None] = mapped_column(String, nullable=True)
    thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default='pending')
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trace: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

class Note(Base):
    __tablename__ = 'note'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, unique=True)
    lesson_id: Mapped[str] = mapped_column(ForeignKey('lesson.id'))
    block_id: Mapped[str] = mapped_column(String)
    anchor: Mapped[dict] = mapped_column(JSON)
    text: Mapped[str] = mapped_column(Text)

class Progress(Base):
    __tablename__ = 'progress'
    course_id: Mapped[str] = mapped_column(ForeignKey('course.id'), primary_key=True)
    concept_id: Mapped[str] = mapped_column(String)
    lesson_id: Mapped[str | None] = mapped_column(String, nullable=True)
    block_id: Mapped[str | None] = mapped_column(String, nullable=True)
    offset: Mapped[int] = mapped_column(Integer, default=0)
    operation_id: Mapped[str | None] = mapped_column(String, nullable=True)

class Exercise(Base):
    __tablename__ = 'exercise'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    concept_id: Mapped[str] = mapped_column(ForeignKey('concept.id'))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    hint: Mapped[str] = mapped_column(Text)

class Attempt(Base):
    __tablename__ = 'attempt'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    operation_id: Mapped[str] = mapped_column(String, unique=True)
    exercise_id: Mapped[str] = mapped_column(ForeignKey('exercise.id'))
    response: Mapped[str] = mapped_column(Text)
    feedback: Mapped[str] = mapped_column(Text)
    hint_used: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(String, default='fixed_answer')
    data: Mapped[dict] = mapped_column(JSON, default=dict)

class CourseFolder(Base):
    __tablename__ = 'course_folder'
    folder: Mapped[str] = mapped_column(String, primary_key=True)
    course_id: Mapped[str] = mapped_column(ForeignKey('course.id'), unique=True)

class MaterialFile(Base):
    __tablename__ = 'material_file'
    material_id: Mapped[str] = mapped_column(ForeignKey('material.id'), primary_key=True)
    path: Mapped[str] = mapped_column(Text)

class MaterialPlan(Base):
    __tablename__ = 'material_plan'
    material_id: Mapped[str] = mapped_column(ForeignKey('material.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String, default='pending')
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)

class PracticeGeneration(Base):
    __tablename__ = 'practice_generation'
    operation_id: Mapped[str] = mapped_column(String, primary_key=True)
    exercise_id: Mapped[str] = mapped_column(ForeignKey('exercise.id'))
    metrics: Mapped[dict] = mapped_column(JSON)

class SchemaVersion(Base):
    __tablename__='schema_version'
    id: Mapped[str] = mapped_column(String,primary_key=True)

class ConceptAlias(Base):
    __tablename__='concept_alias'
    alias: Mapped[str] = mapped_column(String,primary_key=True)
    context: Mapped[str] = mapped_column(String,primary_key=True,default='')
    concept_id: Mapped[str] = mapped_column(ForeignKey('concept.id'),primary_key=True)
    source: Mapped[str] = mapped_column(String,default='confirmed')
    language: Mapped[str] = mapped_column(String,default='auto')

class MatchCache(Base):
    __tablename__='match_cache'
    key: Mapped[str] = mapped_column(String,primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)

class StudyRun(Base):
    __tablename__='study_run'
    id: Mapped[str] = mapped_column(String,primary_key=True)
    course_id: Mapped[str] = mapped_column(String)
    concept_id: Mapped[str] = mapped_column(String)
    lesson_id: Mapped[str] = mapped_column(String)
    completed: Mapped[bool] = mapped_column(default=False)
    review: Mapped[bool] = mapped_column(default=False)

class AnalysisCache(Base):
    __tablename__='analysis_cache'
    key: Mapped[str] = mapped_column(String,primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)

class TopicSource(Base):
    __tablename__='topic_source'
    topic_id: Mapped[str] = mapped_column(String,primary_key=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey('source_chunk.id'),primary_key=True)

class LessonGeneration(Base):
    __tablename__ = 'lesson_generation'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    course_id: Mapped[str] = mapped_column(String)
    concept_id: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='queued')
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
