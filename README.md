
# Employee Attendance & Analytics API

FastAPI + MongoDB. All the code is in app/main.py.

## How to run
1. pip install -r requirements.txt
2. Set the environment variables MONGO_URI and MONGO_DB (a local .env file also works).
3. uvicorn app.main:app --port 8000

Indexes are created at startup. GET /health returns 200 only if MongoDB answers.

## What is done
Review fixes (see REVIEW.md), punch-in, punch-out, regularization (PATCH) with history, employee monthly summary, department summary, late leaderboard, department trend, and the explain endpoint for all five queries.

## What I did not test
- I tested by hand on the 9 sample documents only.
- I did not run a test on 100,000 records.
- I did not run parallel requests; concurrency safety relies on a unique index and conditional updates.
- Ties in the leaderboard, weekend records and overnight shifts are covered by the code but I did not test them with my own data.
