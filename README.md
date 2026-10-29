CLI utility for the Human.UA API.

Features:
- Authenticate with the API;
- Retrieve and use a `user_id` for API requests;
- Get a list of classes;
- Retrieve students for a specific class;
- Export students from all classes to CSV;
- Create competencies for all classes based on subject names and identified industries;
- Select specific classes for bulk operations (exporting data or creating competencies);
- Assign result groups to selected classes, with options to specify the semester (1/2) and the grading mode (`max` or `individual`);
- Enable/disable the automatic saving of all API HTTP responses to files.

## Configuration

Before running the utility, set the following environment variables:

- `HUMAN_UA_EMAIL`
- `HUMAN_UA_PASSWORD`

## Execution

```powershell
python app.py
```

Once launched, an interactive menu will appear, offering various API-related functions.

## Saving API Responses

- The menu includes a toggle option for automatically saving API responses.
- When enabled, each request is saved as a separate file in the `responses` folder (located next to `app.py`).
- When disabled, no files are created.

## CSV Export

- CSV files are saved to the `exports` folder (located next to `app.py`).
- During the export process, the console displays progress and the full path to each saved file.

## Report on Problematic Student Entries

- When assigning result groups, a CSV report is generated in the `reports` folder.
- The report includes the following details: `class_id`, `semestr_id`, `semester_number`, `student_id`, `mode`, `failed_posts`, and `reasons`.
