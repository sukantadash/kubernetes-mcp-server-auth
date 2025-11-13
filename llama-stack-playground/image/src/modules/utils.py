# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the terms described in the LICENSE file in
# the root directory of this source tree.

import base64
import os
from werkzeug.utils import secure_filename

import pandas as pd


def process_dataset(file):
    if file is None:
        return "No file uploaded", None

    try:
        # Determine file type and read accordingly
        filename = secure_filename(file.filename)
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext == ".csv":
            df = pd.read_csv(file)
        elif file_ext in [".xlsx", ".xls"]:
            df = pd.read_excel(file)
        else:
            return "Unsupported file format. Please upload a CSV or Excel file.", None

        return df

    except Exception as e:
        raise ValueError(f"Error processing file: {str(e)}")


def data_url_from_file(file) -> str:
    file_content = file.read()
    base64_content = base64.b64encode(file_content).decode("utf-8")
    
    # Try to detect MIME type from filename
    filename = secure_filename(file.filename)
    mime_type = "application/octet-stream"
    if filename.endswith('.pdf'):
        mime_type = "application/pdf"
    elif filename.endswith('.txt'):
        mime_type = "text/plain"
    elif filename.endswith('.doc'):
        mime_type = "application/msword"
    elif filename.endswith('.docx'):
        mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    data_url = f"data:{mime_type};base64,{base64_content}"

    return data_url
