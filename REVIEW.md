# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|

| 1 | list_employees, skip line | Pagination skips the wrong number of records: skip = page * page_size. Page 1 skips the first page_size employees instead of starting from the first record (R10: page starts at 1). | GET /employees?page=1&page_size=2 returns EMP0003 and EMP0004 instead of EMP0001 and EMP0002. | Changed to skip = (page - 1) * page_size, so page 1 skips 0 records. |

| 2 | list_employees, count_documents call | total ignores the filter: count_documents({}) counts every employee even when department is given. The contract says total must reflect the applied filters. | GET /employees?department=Engineering returned 2 items but total: 6. | Changed to count_documents(q), the same filter used by the find query. |

| 3 | list_employees, find call | No sort order: the query has no .sort(), so MongoDB can return employees in any order. The contract says sorted by emp_code ascending, and without a stable sort, pages can repeat or skip employees. | GET /employees?page=1&page_size=2 returns employees in insertion order, not guaranteed to be EMP0001, EMP0002. | Added .sort("emp_code", 1) before .skip(skip). |

| 4 | health endpoint | The endpoint always returns 200 and never checks MongoDB. The contract says it must return 200 only if MongoDB answers a ping, otherwise 503. | Make Atlas unreachable (for example a wrong MONGO_URI): GET /health still returns 200. | The endpoint now runs client.admin.command("ping") and returns 503 if it fails. |

| 5 | list_attendance, docs = list(...) and docs.sort lines | The endpoint loads every matching attendance record into Python memory, then sorts and slices there. This is slow and uses a lot of memory on 100,000 records. Also, the sort is only by date; the contract needs date descending, then emp_code ascending. | Seed about 100,000 attendance records and call GET /attendance: the response is very slow. With two records on the same date, the order by emp_code is not guaranteed. | Used count_documents(q) for total, and moved sort, skip and limit into the MongoDB query: sort([("date", -1), ("emp_code", 1)]).skip(skip).limit(page_size). |


| 6 | list_attendance, response items | Older records can be missing the half_day and history fields. The endpoint returned them as stored, so the response did not always have every field in the contract (missing means half_day is false and history is empty). | GET /attendance?emp_code=EMP0002 returned a record with no half_day and no history. | Added d.setdefault("half_day", False) and d.setdefault("history", []) for each returned record. |

| 7 | compute_work_hours | Uses Python round(), which does not round half-up. R4 says work_hours must be rounded to 2 decimals half-up. | punch_in 09:00:00 and punch_out 09:07:30 is 450 seconds, which is exactly 0.125 hours. The starter returns 0.12 but it should be 0.13. | Compute the hours with Decimal (seconds / 3600) and quantize to 0.01 with ROUND_HALF_UP. |

| 8 | compute_late_minutes | Three problems. The check is "minutes > 10" after int-flooring, so 09:40:01 gives 601 / 60 = 10, which is not > 10, and returns 0 (R2 says strictly more than 10:00 is late). The shift start is applied with replace() on the punch-in datetime in its own clock, with no IST conversion, so UTC datetimes from MongoDB are compared with the shift start as if it was UTC. The date is taken from the punch-in, not the attendance date. | Shift 09:30, punch-in 09:40:01 IST should give late_minutes 10 but the starter gives 0. A punch-in at 10:30 IST stored as 05:00 UTC is compared with 09:30 UTC and gives 0 instead of 60. | Convert punch_in to IST, truncate to whole seconds, build shift start on the attendance date in IST, and return seconds // 60 only when seconds > 600. |

| 9 | compute_overtime | Two problems. It returns every positive minute, but R3 says overtime counts only when it is at least 30 minutes, otherwise 0. It also puts the shift end on the attendance date, so for an overnight shift (for example 22:00-06:00) the end is a day too early. | Shift end 18:30, punch-out 18:45 should give 0 but the starter gives 15. Overnight shift 22:00-06:00 on date 2026-07-06 with punch-out at 06:40 IST on 07-07 should give 40 but the starter gives 1480. | Added the 30 minute minimum, and move the shift end to the next day when shift_end <= shift_start. The result is whole minutes (floor) from IST-aware times. |

| 10 | punch_in, employee lookup | emp is None when the employee does not exist, so emp["shift_start"] raises a TypeError and the API returns 500. The contract says 404 for an unknown employee. | POST /attendance/punch-in with emp_code EMP9999 returns 500 Internal Server Error. | Added a check: if the employee is not found, raise HTTPException 404. |


| 11 | punch_in, date and time handling | Uses datetime.fromtimestamp and datetime.now, which use the server's local time zone, so the attendance date is not the IST date (R1). The overnight shift rule is ignored and instants are not truncated to whole seconds. The response also returns the raw datetime and an extra id field, but the contract says epoch milliseconds and no id. | Punch-in at 22:30 IST on a server running in UTC gets the wrong date. Overnight shift 22:00-06:00 with punch-in at 05:30 IST should belong to the previous day. | Convert punched_at from milliseconds to a UTC datetime truncated to whole seconds, compute the date in IST with the overnight rule (attendance_date), and return punch_in as epoch milliseconds without id. |


| 12 | punch_in, duplicate check | Check-then-insert race: find_one and insert_one are separate steps, so two simultaneous requests can both pass the check and both insert. The contract needs exactly one 201 and the rest 409. | Send two identical punch-in requests at the same time: both return 201 and two records exist for the same day. | Created a unique index on (emp_code, date) at startup and insert directly, catching DuplicateKeyError to return 409. |

| 13 | PunchInIn model | No validation on the request body. status is a free string, so any text such as "XYZ" is accepted and stored, and punched_at accepts any integer, including a value in seconds. The contract allows only PRESENT, WFH and ON_DUTY, and punched_at must be epoch milliseconds (at least 100000000000), otherwise 422. | POST /attendance/punch-in with status "XYZ" returns 201 instead of 422. punched_at 1700000000 (seconds) is accepted and stored as a date in 1970. | status is now Literal["PRESENT", "WFH", "ON_DUTY"], and punched_at is a StrictInt with ge=100000000000 and le=4102444800000, so invalid values return 422. |

| 14 | list_attendance, response items | The response returned an extra id field (the MongoDB _id as text) and punch_in and punch_out as datetime values, which are serialized as text like "2026-07-14T04:02:00". The contract says _id never appears in a response and instants are integer epoch milliseconds. | GET /attendance?emp_code=EMP0002 returned "punch_in": "2026-07-14T04:02:00" instead of a number. | Excluded _id in the query projection and converted punch_in and punch_out to epoch milliseconds with dt_to_ms before returning. |

| 15 | EmployeeIn model | No validation on the request body. emp_code accepts any text, email and shift times are not checked, joined_on can be any text, and shift_start can equal shift_end. The contract needs emp_code like EMP followed by 4 to 6 digits, a valid email, shift times as HH:MM, a real date for joined_on, and shift_start different from shift_end, otherwise 422. | POST /employees with emp_code "abc" returns 201 instead of 422. shift_start "25:99" is accepted and later breaks the late calculation. | Added Field patterns and length limits, a validator that parses joined_on as a date, and a model validator that rejects shift_start equal to shift_end. |

| 16 | create_employee, duplicate check | Check-then-insert race: find_one and insert_one are separate steps, so two simultaneous requests with the same emp_code can both pass the check. The contract says the unique index must enforce this, with exactly one 201 and the rest 409. | Send two POST /employees requests with the same emp_code at the same time: both can return 201 and create two employees. | Created a unique index on emp_code at startup, removed the find_one check, and catch DuplicateKeyError on insert_one to return 409. |


| 17 | create_employee and list_employees, created_at | created_at was stored with datetime.now() (server local time, no time zone) and returned as text like "2026-01-05T04:30:00". The contract says epoch milliseconds. | POST /employees or GET /employees returns "created_at": "2026-01-05T04:30:00" instead of a number. | Store created_at as a UTC datetime truncated to seconds, and convert it with dt_to_ms before returning it from both endpoints. |

| 18 | list_employees and list_attendance, page and page_size | page and page_size are plain ints with no limits. page=0 gives a negative skip value, which MongoDB rejects, so the API returns 500. page_size=0 or a huge page_size is accepted. The contract says page starts at 1 and page_size is between 1 and 100. | GET /employees?page=0 returns 500. GET /employees?page_size=500 returns 500 records. | Used Query(1, ge=1) for page and Query(20, ge=1, le=100) for page_size in both endpoints, so invalid values return 422. |


| 19 | list_attendance, filters | date_from, date_to and status are free text with no validation. A malformed date or an unknown status is accepted, and date_from after date_to is not rejected. The contract says date_from after date_to is 422. | GET /attendance?date_from=2026-07-20&date_to=2026-07-01 returns 200 with no records instead of 422. | date_from and date_to must match YYYY-MM-DD, status must be one of the five valid values, and date_from after date_to raises 422. |


Also note anything you looked at and decided was **not** a defect, and why.
