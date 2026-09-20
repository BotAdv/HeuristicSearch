# -*- coding: utf-8 -*-
"""页面路由。"""
from __future__ import annotations

from flask import Blueprint, render_template

pages = Blueprint("pages", __name__)


@pages.get("/")
def index():
    return render_template("index.html", page="sandbox", title="沙盒")


@pages.get("/strategies")
def strategies_page():
    return render_template("strategies.html", page="strategies", title="策略库")


@pages.get("/lab")
def lab_page():
    return render_template("lab.html", page="lab", title="模拟实验台")


@pages.get("/human")
def human_page():
    return render_template("human.html", page="human", title="逐步猜测")
